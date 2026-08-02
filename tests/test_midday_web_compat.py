from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from backend.api import midday


class _Session:
    def __init__(self, official, rows):
        self._scalars = iter((None, None, official))
        self._rows = rows
        self.closed = False

    def scalar(self, _query):
        return next(self._scalars)

    def scalars(self, _query):
        return self._rows

    def close(self):
        self.closed = True


def test_v22_status_falls_back_to_current_midday_run(monkeypatch):
    official = SimpleNamespace(
        run_id="midday-current",
        session_trade_date=date(2026, 7, 23),
        decision_time=datetime(2026, 7, 23, 11, 32),
        status="WAITING_AFTERNOON_RECHECK",
        current_stage="WAITING_AFTERNOON_RECHECK",
        baseline_trade_date=date(2026, 7, 22),
        base_pool_count=104,
        snapshot_count=30,
        minute_count=30,
        flash_count=30,
        pro_count=20,
        final_count=20,
        held_count=4,
        excel_path="today.xlsx",
    )
    rows = [
        SimpleNamespace(
            hard_gate_status="PASS", position_status="UNKNOWN",
            pro_rank=1, candidate_action="KEEP_WATCH",
        ),
        SimpleNamespace(
            hard_gate_status="PASS", position_status="HELD",
            pro_rank=None, candidate_action="REMOVE_FROM_POOL",
        ),
    ]
    session = _Session(official, rows)
    monkeypatch.setattr(midday, "_session", lambda: session)

    response = midday.midday_v22_status(
        SimpleNamespace(state=SimpleNamespace(trace_id="trace")),
        date(2026, 7, 23),
    )

    assert response["data"]["run_id"] == "midday-current"
    assert response["data"]["compatibility_source"] == "CURRENT_MIDDAY"
    assert response["data"]["counts"]["result_layers"] == {"AFTERNOON_WATCH": 2}
    assert session.closed is True


def test_current_midday_result_is_adapted_for_existing_web_table():
    payload = midday._official_result_payload({
        "stock_code": "600797.SH",
        "stock_name": "浙大网新",
        "position_status": "UNKNOWN",
        "base_quant_rank": 5,
        "base_quant_score": 61.52,
        "midday_enhanced_score": 63.06,
        "flash_decision": "WATCH",
        "pro_rank": 3,
        "hard_gate_status": "PASS",
        "candidate_action": "KEEP_WATCH",
        "feature_scope": "MORNING_FULL_MINUTE",
        "key_risks": ["liquidity"],
        "recommended_price": 6.99,
        "max_acceptable_price": 7.03,
        "stop_loss": 6.90,
    })

    assert payload["result_layer"] == "AFTERNOON_WATCH"
    assert payload["quant_rank"] == 5
    assert payload["quant_score"] == 61.52
    assert payload["afternoon_recheck"]["current_price"] == 6.99
    assert payload["compatibility_source"] == "CURRENT_MIDDAY"
