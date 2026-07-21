from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from database.base import Base
from database.models.entry_timing import AdmissionRun, EntryTimingResult
from database.models.entry_timing_v2 import AdmissionV2Run, EntryTimingV2Result
from database.models.quant_run import QuantRankResult, QuantRun
from database.models.stock import StockMaster
from database.session import create_engine_from_url, get_session
from entry_timing.service import EntryTimingShadowService
from entry_timing.service_v2 import EntryTimingV2ShadowService


TRADE_DATE = date(2026, 7, 17)


def _write_cache(root) -> None:
    dates = [TRADE_DATE - timedelta(days=offset) for offset in range(14, -1, -1)]
    for index, day in enumerate(dates):
        key = day.strftime("%Y%m%d")
        daily = root / "trade_date" / "daily" / f"{key}.json"
        adj = root / "trade_date" / "adj_factor" / f"{key}.json"
        daily.parent.mkdir(parents=True, exist_ok=True)
        adj.parent.mkdir(parents=True, exist_ok=True)
        daily.write_text(json.dumps([{
            "ts_code": "000001.SZ", "trade_date": key, "close": 10 + index * 0.03,
            "pre_close": 10 + max(0, index - 1) * 0.03, "pct_chg": 0.3, "amount": 250000,
        }]), encoding="utf-8")
        adj.write_text(json.dumps([{"ts_code": "000001.SZ", "adj_factor": 1.0}]), encoding="utf-8")
    current = TRADE_DATE.strftime("%Y%m%d")
    payloads = {
        "daily_basic": [{"ts_code": "000001.SZ", "turnover_rate": 4.0}],
        "moneyflow": [{"ts_code": "000001.SZ", "net_mf_amount": 1000}],
        "stk_limit": [{"ts_code": "000001.SZ", "up_limit": 12.0, "down_limit": 8.0}],
    }
    for name, rows in payloads.items():
        path = root / "trade_date" / name / f"{current}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows), encoding="utf-8")


def _database(tmp_path):
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'entry.db').as_posix()}")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    run = QuantRun(
        run_id="quant-entry-test", request_hash="entry-test-hash", run_mode="HISTORICAL_REPLAY",
        decision_time=datetime(2026, 7, 17, 15, 30, tzinfo=timezone.utc),
        base_market_trade_date=TRADE_DATE, target_trade_date=TRADE_DATE + timedelta(days=1),
        factor_version="unchanged-v1", config_snapshot={}, data_manifest_id="manifest-entry-test",
        universe_count=1, filtered_count=1, scored_count=1, top_count=1,
        no_llm_call_verified=True, trade_date_cache_used=True, per_stock_api_call_count=0,
        temporal_status="PASS", actionable=True, status="COMPLETED",
    )
    rank = QuantRankResult(
        quant_run_id=run.run_id, stock_code="000001.SZ", rank=1, total_score=70,
        technical_score=70, capital_score=70, emotion_score=70, momentum_score=70, risk_score=70,
    )
    session.add_all([run, rank, StockMaster(code="000001.SZ", name="平安银行", market="SZ", industry="银行", status="L")])
    session.commit()
    return engine, session


def test_shadow_run_is_local_idempotent_persistent_and_quant_immutable(tmp_path, monkeypatch) -> None:
    cache_root = tmp_path / "cache"
    _write_cache(cache_root)
    engine, session = _database(tmp_path)

    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("network call is forbidden")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden_network)
    service = EntryTimingShadowService(session, cache_root=cache_root)
    first = service.run(TRADE_DATE, force_shadow=True)
    second = service.run(TRADE_DATE, force_shadow=True)
    assert first["run_id"] == second["run_id"]
    assert first["quant_hash_unchanged"] is True
    assert first["llm_calls"] == first["external_api_calls"] == 0
    assert first["admitted_count"] <= 20
    assert session.scalar(select(func.count()).select_from(AdmissionRun)) == 1
    assert session.scalar(select(func.count()).select_from(EntryTimingResult)) == 1
    original_quant_score = session.scalar(select(QuantRankResult.total_score))
    session.close()

    fresh = get_session(engine)
    assert fresh.scalar(select(func.count()).select_from(AdmissionRun)) == 1
    assert fresh.scalar(select(QuantRankResult.total_score)) == original_quant_score
    persisted = fresh.scalar(select(EntryTimingResult))
    persisted.admission_status = "BLOCK" if persisted.admission_status != "BLOCK" else "PASS"
    with pytest.raises(ValueError, match="IMMUTABLE_ENTRY_TIMING_SNAPSHOT"):
        fresh.commit()
    fresh.rollback()
    fresh.close()
    engine.dispose()


def test_shadow_run_requires_explicit_force_while_disabled(tmp_path) -> None:
    cache_root = tmp_path / "cache"
    _write_cache(cache_root)
    engine, session = _database(tmp_path)
    with pytest.raises(ValueError, match="ENTRY_TIMING_DISABLED_SHADOW_ONLY"):
        EntryTimingShadowService(session, cache_root=cache_root).run(TRADE_DATE)
    session.close()
    engine.dispose()


def test_v2_shadow_is_local_idempotent_isolated_and_creates_no_orders(tmp_path, monkeypatch) -> None:
    cache_root = tmp_path / "cache"
    _write_cache(cache_root)
    engine, session = _database(tmp_path)

    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("network call is forbidden")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden_network)
    service = EntryTimingV2ShadowService(session, cache_root=cache_root)
    first = service.run(TRADE_DATE, candidate_mode="QUANT_TOP100", force_shadow=True)
    second = service.run(TRADE_DATE, candidate_mode="QUANT_TOP100", force_shadow=True)
    assert first["run_id"] == second["run_id"]
    assert first["quant_hash_unchanged"] is True
    assert first["llm_calls"] == first["external_api_calls"] == first["order_creation_count"] == 0
    assert first["shadow_only"] is True and first["enabled_in_production"] is False
    assert session.scalar(select(func.count()).select_from(AdmissionRun)) == 1
    assert session.scalar(select(func.count()).select_from(AdmissionV2Run)) == 1
    assert session.scalar(select(func.count()).select_from(EntryTimingV2Result)) == 1
    assert session.scalar(select(EntryTimingV2Result.stock_name)) == "平安银行"
    session.close()

    fresh = get_session(engine)
    assert fresh.scalar(select(func.count()).select_from(AdmissionV2Run)) == 1
    assert fresh.scalar(select(func.count()).select_from(EntryTimingV2Result)) == 1
    fresh.close()
    engine.dispose()
