from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.order_plan import OrderPlan, OrderPlanEvaluation
from database.session import create_engine_from_url, get_session
from datasource.schemas import KlineBar
from review.order_plan_evaluator import OrderPlanEvaluator


class StubDataSource:
    def __init__(self, bar: KlineBar) -> None:
        self.bar = bar

    def get_kline(self, stock_code, start_date, end_date, frequency="1d"):
        return [self.bar]


def make_bar(open_price: str = "10.00", high: str = "10.50", low: str = "9.90", close: str = "10.20") -> KlineBar:
    return KlineBar(
        stock_code="000001",
        trade_date=date(2026, 1, 5),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        pre_close=Decimal(open_price),
        volume=1000000,
        amount=Decimal("10000000"),
    )


def add_plan(session, price: str, max_acceptable: str | None = None) -> OrderPlan:
    plan = OrderPlan(
        stock_code="000001",
        plan_date=date(2026, 1, 5),
        side="BUY",
        strategy_type="BALANCED",
        recommended_price=Decimal(price),
        max_acceptable_price=Decimal(max_acceptable) if max_acceptable else None,
        status="DRAFT",
    )
    session.add(plan)
    session.commit()
    return plan


def run_case(price: str, bar: KlineBar, max_acceptable: str | None = None):
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)
    try:
        plan = add_plan(session, price, max_acceptable=max_acceptable)
        result = OrderPlanEvaluator(
            session=session,
            data_source_service=StubDataSource(bar),
        ).evaluate_order_plan(plan.id, date(2026, 1, 5))
        evaluations = session.scalars(select(OrderPlanEvaluation)).all()
        return result, evaluations
    finally:
        session.close()
        engine.dispose()


def test_buy_order_plan_fills_inside_daily_range() -> None:
    result, evaluations = run_case("10.10", make_bar())

    assert result.was_filled is True
    assert result.simulated_fill_price == Decimal("10.10")
    assert result.price_quality_score >= Decimal("80")
    assert len(evaluations) == 1


def test_unfilled_conservative_price_records_missed_opportunity() -> None:
    result, _ = run_case("9.80", make_bar(low="10.00", close="10.40"))

    assert result.was_filled is False
    assert result.missed_opportunity is True
    assert "missed" in result.evaluation_reason.lower()


def test_unfilled_plan_can_record_risk_avoided() -> None:
    result, _ = run_case("9.80", make_bar(low="10.00", close="9.60"))

    assert result.was_filled is False
    assert result.risk_avoided is True


def test_price_above_max_acceptable_reduces_quality() -> None:
    result, _ = run_case("10.30", make_bar(), max_acceptable="10.10")

    assert result.was_filled is True
    assert result.price_quality_score < Decimal("80")
