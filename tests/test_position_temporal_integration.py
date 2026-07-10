from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.api.v1.position_sizing import PositionSizingRequest, _validate_temporal_inputs
from database.base import Base
from database.models.quant_run import QuantRun
from database.models.temporal import RunDataManifestRecord
from database.session import create_engine_from_url, get_session


def _body(snapshot):
    return PositionSizingRequest(
        quant_run_id="q1", run_data_manifest_id="m1", account_snapshot_time=snapshot,
        account={"equity": "100000", "available_cash": "80000"},
        candidates=[{"stock_code": "000001", "final_score": "80", "entry_price": "10", "stop_price": "9.3", "average_daily_amount": "1000000"}],
    )


def test_position_sizing_requires_matching_actionable_manifest_and_fresh_account():
    engine = create_engine_from_url("sqlite:///:memory:"); Base.metadata.create_all(engine); session = get_session(engine)
    decision = datetime(2026, 7, 9, 12, tzinfo=timezone.utc)
    session.add(RunDataManifestRecord(manifest_id="m1", run_id="t1", run_mode="HISTORICAL_REPLAY", decision_time=decision, base_market_trade_date=date(2026, 7, 9), target_trade_date=date(2026, 7, 10), fundamental_cutoff_time=decision, required_dataset_watermarks=[], optional_dataset_watermarks=[], temporal_status="PASS", actionable=True, block_reasons=[], warnings=[]))
    session.add(QuantRun(run_id="q1", request_hash="h1", run_mode="HISTORICAL_REPLAY", decision_time=decision, base_market_trade_date=date(2026, 7, 9), target_trade_date=date(2026, 7, 10), config_snapshot={}, data_manifest_id="m1", temporal_status="PASS", actionable=True, status="COMPLETED", no_llm_call_verified=True, top_count=500))
    session.commit()
    assert _validate_temporal_inputs(session, _body(decision - timedelta(minutes=10))) is None
    assert _validate_temporal_inputs(session, _body(decision - timedelta(hours=1))) == "ACCOUNT_SNAPSHOT_STALE"
    session.close(); engine.dispose()
