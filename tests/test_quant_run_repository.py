from datetime import date, datetime, timezone

from database.base import Base
from database.session import create_engine_from_url, get_session
from quant.run_repository import QuantRunRepository


def _report():
    item = {"rank": 1, "stock_code": "000001.SZ", "total_score": 80, "technical_score": 80, "capital_score": 80, "emotion_score": 80, "momentum_score": 80, "risk_score": 80}
    return {"run_id": "q1", "factor_version": "v1", "universe_count": 5000, "filtered_count": 4900, "scored_count": 4900, "skipped_count": 100, "top_count": 3, "no_llm_call_verified": True, "trade_date_cache_used": True, "per_stock_api_call_count": 0, "performance": {"total_seconds": 10}, "top_stocks": [item, {**item, "rank": 2, "stock_code": "000002.SZ"}, {**item, "rank": 3, "stock_code": "000003.SZ"}]}


def test_quant_run_ranking_idempotency_latest_and_samples():
    engine = create_engine_from_url("sqlite:///:memory:"); Base.metadata.create_all(engine); session = get_session(engine)
    repo = QuantRunRepository(session); kwargs = dict(run_mode="HISTORICAL_REPLAY", decision_time=datetime(2026, 7, 10, tzinfo=timezone.utc), base_trade_date=date(2026, 7, 9), target_trade_date=date(2026, 7, 10), manifest_id="m1", temporal_status="PASS", actionable=True, config_snapshot={"top_n": 3})
    first = repo.save_report(_report(), **kwargs); second = repo.save_report(_report(), **kwargs)
    assert first.id == second.id
    assert repo.latest_actionable().run_id == "q1"
    assert [row.rank for row in repo.samples("q1")] == [1, 2, 3]
    session.close(); engine.dispose()
