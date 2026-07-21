from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine

from database.models import OrderPlan
from database.session import get_session, init_db
from post_close.coverage import IFindTieredCoverageService
from post_close.gates import PositionTruthGate, PostCloseActionPoolResolver
from post_close.positions import PositionImportService


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return get_session(engine)


def test_position_truth_requires_explicit_confirmation_and_accepts_explicit_empty():
    session = make_session()
    gate = PositionTruthGate(session)
    assert gate.evaluate(date(2026, 7, 15))["status"] == "MISSING"
    assert gate.evaluate(date(2026, 7, 15))["action_run_allowed"] is False
    service = PositionImportService(session)
    service.confirm_empty(account_scope="HUMAN_REFERENCE", trade_date_value=date(2026, 7, 15))
    result = gate.evaluate(date(2026, 7, 15))
    assert result["status"] == "CONFIRMED_EMPTY"
    assert result["confirmed_empty"] is True
    assert result["action_run_allowed"] is True
    assert result["required_scopes"] == ["HUMAN_REFERENCE"]
    assert result["scope_status"]["AI_SIMULATION"] == "NOT_REQUIRED"
    session.close()


def test_position_truth_requires_ai_when_enabled_or_explicitly_selected():
    session = make_session()
    service = PositionImportService(session)
    service.confirm_empty(account_scope="HUMAN_REFERENCE", trade_date_value=date(2026, 7, 15))
    enabled = PositionTruthGate(session, ai_simulation_enabled=True).evaluate(date(2026, 7, 15))
    assert enabled["required_scopes"] == ["AI_SIMULATION", "HUMAN_REFERENCE"]
    assert enabled["scope_status"]["AI_SIMULATION"] == "MISSING"
    assert enabled["action_run_allowed"] is False
    selected = PositionTruthGate(session, requested_scopes={"AI_SIMULATION"}).evaluate(date(2026, 7, 15))
    assert selected["ai_simulation_required"] is True
    session.close()


def test_position_truth_rejects_stale_confirmation():
    session = make_session()
    service = PositionImportService(session)
    service.confirm_empty(account_scope="HUMAN_REFERENCE", trade_date_value=date(2026, 7, 1))
    result = PositionTruthGate(
        session,
        now=datetime(2026, 7, 15, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    ).evaluate(date(2026, 7, 15))
    assert result["status"] == "STALE"
    assert result["action_run_allowed"] is False
    session.close()


def test_action_pool_counts_active_order_plan_as_visible_source_and_excludes_other_dates():
    session = make_session()
    session.add_all([
        OrderPlan(stock_code="000001.SZ", plan_date=date(2026, 7, 15), status="ACTIVE"),
        OrderPlan(stock_code="000002.SZ", plan_date=date(2026, 7, 14), status="ACTIVE"),
        OrderPlan(stock_code="000003.SZ", plan_date=date(2026, 7, 15), status="CANCELLED"),
    ])
    session.commit()
    result = PostCloseActionPoolResolver(session).resolve(date(2026, 7, 15), mode="POST_CLOSE_FINAL")
    assert result["active_order_plan_count"] == 1
    assert result["raw_union_count"] == result["deduplicated_count"] == 1
    assert result["items"][0]["origins"] == ["ACTIVE_ORDER_PLAN"]
    assert result["invariant_status"] == "PASS"
    session.close()


def test_minute_coverage_priority_puts_human_holdings_first():
    session = make_session()
    service = IFindTieredCoverageService(session)
    service.coverage_config = {"max_full_minute_stocks": 2}
    pool = {"items": [
        {"stock_code": "000001.SZ", "origins": ["FINAL"]},
        {"stock_code": "000002.SZ", "origins": ["HUMAN_HELD"]},
        {"stock_code": "000003.SZ", "origins": ["ACTIVE_ORDER_PLAN"]},
    ]}
    result = service.evaluate(date(2026, 7, 15), pool, None)
    assert result["priority_order"] == ["000002.SZ", "000003.SZ"]
    assert result["skipped_by_quota"] == 1
    assert result["ab_comparability"] == "NOT_COMPARABLE_COVERAGE_MISMATCH"
    session.close()
