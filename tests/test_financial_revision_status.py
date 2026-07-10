from __future__ import annotations

from datetime import datetime, timezone

from fundamentals.financial_status import FinancialStatusEngine
from fundamentals.revision import prefer_financial_publication, select_latest_revisions


def test_revision_selection_excludes_future_and_prefers_updated_revision():
    rows = [
        {"ts_code": "000001.SZ", "end_date": "20241231", "report_type": "1", "f_ann_date": "20250301", "update_flag": "0", "revenue": 1},
        {"ts_code": "000001.SZ", "end_date": "20241231", "report_type": "1", "f_ann_date": "20250310", "update_flag": "1", "revenue": 2},
        {"ts_code": "000001.SZ", "end_date": "20241231", "report_type": "1", "f_ann_date": "20250410", "update_flag": "1", "revenue": 3},
    ]
    selected = select_latest_revisions(rows, datetime(2025, 3, 20, tzinfo=timezone.utc))
    assert len(selected) == 1
    assert selected[0]["revenue"] == 2
    assert selected[0]["available_at"].startswith("2025-03-10")


def test_publication_priority_formal_over_express_over_forecast():
    assert prefer_financial_publication({"x": 1}, {"x": 2}, {"x": 3})[0] == "FORMAL"
    assert prefer_financial_publication(None, {"x": 2}, {"x": 3})[0] == "EXPRESS"


def test_financial_status_engine_covers_core_states():
    engine = FinancialStatusEngine()
    assert engine.evaluate({}).status == "INSUFFICIENT_DATA"
    assert engine.evaluate({"roe": 15, "or_yoy": 10, "netprofit_yoy": 12, "n_cashflow_act": 1}).status == "HEALTHY"
    assert engine.evaluate({"roe": 5, "or_yoy": 2, "netprofit_yoy": 3, "n_cashflow_act": 1}).status == "STABLE"
    assert engine.evaluate({"n_income_attr_p": -1, "n_cashflow_act": -1, "debt_to_assets": 60}).status == "PRESSURED"
    assert engine.evaluate({"debt_to_assets": 90, "roe": 1, "or_yoy": -1}).status == "HIGH_RISK"
