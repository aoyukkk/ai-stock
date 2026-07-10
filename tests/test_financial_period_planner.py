from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fundamentals.period_planner import FinancialPeriodPlanner


SH = ZoneInfo("Asia/Shanghai")


def test_planner_is_dynamic_and_selects_latest_disclosed_as_of_decision():
    planner = FinancialPeriodPlanner()
    decision = datetime(2026, 5, 1, 12, tzinfo=SH)
    periods = planner.candidate_periods(decision)
    assert periods[0] == "20260331"
    assert len(periods) == 8
    selected, metadata = planner.select_latest({
        "20251231": [{"ts_code": "000001.SZ", "end_date": "20251231", "report_type": "1", "f_ann_date": "20260320", "update_flag": "0"}],
        "20260331": [{"ts_code": "000001.SZ", "end_date": "20260331", "report_type": "1", "f_ann_date": "20260420", "update_flag": "0"}],
    }, "000001.SZ", decision)
    assert selected["end_date"] == "20260331"
    assert metadata["latest_financial_period"] == "20260331"
    assert metadata["is_latest_available_as_of_decision_time"] is True


def test_report_ended_but_not_announced_is_excluded():
    selected, metadata = FinancialPeriodPlanner().select_latest({
        "20260331": [{"ts_code": "000001.SZ", "end_date": "20260331", "f_ann_date": "20260510"}],
        "20251231": [{"ts_code": "000001.SZ", "end_date": "20251231", "f_ann_date": "20260320"}],
    }, "000001.SZ", datetime(2026, 4, 1, tzinfo=SH))
    assert selected["end_date"] == "20251231"
