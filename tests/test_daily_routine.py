from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from database.base import Base
from database.models.validation import ModelValidationRun, ModelValidationSample
from database.models.workbench import ManualSelectionRecord
from database.session import create_engine_from_url, get_session
from backend.workbench.service import WorkbenchService
from scripts.run_daily_routine import (
    _build_monitor,
    _carry_forward_manual_pool,
    _hash_codes,
    _latest_usable_flash,
    _quant_coverage_is_usable,
    _run_close,
)


TRADE_DATE = date(2026, 7, 15)


def _session():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, get_session(engine)


def _flash(run_id: str) -> ModelValidationRun:
    return ModelValidationRun(
        run_id=run_id,
        quant_run_id="quant-current",
        run_data_manifest_id="manifest-current",
        run_mode="POST_MARKET_FINAL",
        knowledge_mode="STRUCTURED_INPUT_ONLY",
        decision_time=datetime(2026, 7, 15, 7, 30, tzinfo=timezone.utc),
        base_market_trade_date=TRADE_DATE,
        target_trade_date=date(2026, 7, 16),
        real_llm=True,
        status="PARTIAL_SUCCESS",
        request_hash=f"hash-{run_id}",
        config_snapshot={
            "flash_batch_quality": {"degenerate": False, "usable_for_final": True},
        },
        expected_universe_audit={},
        warnings=[],
    )


def test_flash_reuse_requires_exact_current_manual_pool():
    engine, session = _session()
    try:
        run = _flash("flash-current")
        session.add(run)
        session.add(ModelValidationSample(
            validation_run_id=run.run_id,
            quant_run_id=run.quant_run_id,
            run_data_manifest_id=run.run_data_manifest_id,
            rank=1,
            stock_code="000001.SZ",
            stock_name="平安银行",
            quant_scores={},
            profile_version="v1",
            selected_at=datetime.now(timezone.utc),
            fundamental_result={},
            screening_result={"_trader_demo": {"manual_selected": True}},
            field_provenance={},
            missing_fields=[],
        ))
        session.commit()

        matched = _latest_usable_flash(
            session, TRADE_DATE, "quant-current", ["000001.SZ"]
        )

        assert matched is not None
        assert matched.run_id == run.run_id
        assert _latest_usable_flash(
            session, TRADE_DATE, "quant-current", ["000002.SZ"]
        ) is None
    finally:
        session.close()
        engine.dispose()


def test_pro_resume_still_requires_explicit_llm_budget_confirmation():
    args = SimpleNamespace(start_stage="pro", confirm_llm_budget=False)

    with pytest.raises(ValueError, match="LLM_BUDGET_CONFIRMATION_REQUIRED"):
        _run_close(args)


def test_manual_pool_hash_is_order_independent_after_sorting():
    assert _hash_codes(sorted(["000002.SZ", "000001.SZ"])) == _hash_codes(
        ["000001.SZ", "000002.SZ"]
    )


def test_quant_reuse_requires_full_market_coverage():
    assert _quant_coverage_is_usable(SimpleNamespace(filtered_count=5318, scored_count=5300))
    assert not _quant_coverage_is_usable(SimpleNamespace(filtered_count=5318, scored_count=1038))


def test_empty_daily_manual_pool_carries_forward_latest_confirmed_pool():
    engine, session = _session()
    try:
        session.add(ManualSelectionRecord(
            trade_date=date(2026, 7, 15),
            stock_code="603726.SH",
            reason="持续观察",
            priority="HIGH",
        ))
        session.commit()

        result = _carry_forward_manual_pool(
            session,
            WorkbenchService(session),
            date(2026, 7, 16),
        )

        current = session.scalar(select(ManualSelectionRecord).where(
            ManualSelectionRecord.trade_date == date(2026, 7, 16)
        ))
        assert result == {"count": 1, "source": "CARRIED_FORWARD:2026-07-15"}
        assert current is not None
        assert current.stock_code == "603726.SH"
    finally:
        session.close()
        engine.dispose()


def test_daily_monitor_defines_two_separate_review_outputs(monkeypatch, tmp_path):
    class Service:
        def __init__(self, session):
            self.count = 0

        def start(self, request):
            self.count += 1
            return {"performance_run_id": f"run-{request.selection_scope}", "job_id": None}

    class Session:
        def close(self):
            return None

    commands = []
    monkeypatch.setattr("scripts.run_daily_routine.get_session", lambda: Session())
    monkeypatch.setattr("scripts.run_daily_routine.SelectionPerformanceService", Service)
    monkeypatch.setattr("scripts.run_daily_routine._checked_command", lambda command: commands.append(command))
    monkeypatch.setattr("scripts.run_daily_routine.ROOT", tmp_path)

    result = _build_monitor(TRADE_DATE)

    assert result["today_recommendation_output"].endswith("今日推荐复盘_截至2026-07-15.xlsx")
    assert result["key_candidates_output"].endswith("重点候选复盘_截至2026-07-15.xlsx")
    assert len(commands) == 2
    assert {command[command.index("--report-kind") + 1] for command in commands} == {
        "today-recommendation",
        "key-candidates",
    }
