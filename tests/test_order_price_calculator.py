from datetime import date, timedelta
from decimal import Decimal

from datasource.schemas import KlineBar
from order_price.config import load_order_price_config
from order_price.order_plan_generator import generate_order_plan
from order_price.schemas import OrderPriceInput


def make_bars(count: int = 20) -> list[KlineBar]:
    start = date(2026, 1, 1)
    bars = []
    for index in range(count):
        price = Decimal("10.00") + Decimal(index) * Decimal("0.02")
        bars.append(
            KlineBar(
                stock_code="000001",
                trade_date=start + timedelta(days=index),
                open=price,
                high=price + Decimal("0.18"),
                low=price - Decimal("0.15"),
                close=price + Decimal("0.03"),
                pre_close=price - Decimal("0.02"),
                volume=1000000 + index,
                amount=(price + Decimal("0.03")) * Decimal(1000000 + index),
            )
        )
    return bars


def make_context(risk_level: str = "LOW", recommendation: str = "WATCH") -> OrderPriceInput:
    return OrderPriceInput(
        stock_code="000001",
        stock_name="Mock Stock",
        industry="mock",
        side="BUY",
        committee_score=Decimal("82"),
        recommendation=recommendation,
        risk_level=risk_level,
        confidence=Decimal("0.80"),
        previous_close=Decimal("10.00"),
        latest_price=Decimal("10.10"),
        limit_up_price=Decimal("11.00"),
        limit_down_price=Decimal("9.00"),
        kline_bars=make_bars(),
        volume=1200000,
        amount=Decimal("12120000"),
        turnover_rate=Decimal("2.50"),
        news_score=Decimal("80"),
        emotion_score=Decimal("75"),
        capital_score=Decimal("78"),
    )


def test_three_price_levels_and_risk_prices_are_generated() -> None:
    plan = generate_order_plan(make_context(), load_order_price_config(), plan_date=date(2026, 1, 21))

    assert len(plan.candidates) == 3
    assert {candidate.price_type for candidate in plan.candidates} == {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}
    assert plan.max_acceptable_price <= Decimal("11.00")
    assert plan.recommended_price is not None
    assert plan.recommended_price <= plan.max_acceptable_price
    assert plan.stop_loss_price < plan.recommended_price
    assert plan.take_profit_1_price > plan.recommended_price
    assert plan.take_profit_2_price > plan.take_profit_1_price


def test_black_swan_outputs_blocked_plan_without_trade_command() -> None:
    plan = generate_order_plan(
        make_context(risk_level="BLACK_SWAN", recommendation="BLOCKED"),
        load_order_price_config(),
        plan_date=date(2026, 1, 21),
    )
    text = plan.model_dump_json().lower()

    assert plan.status == "BLOCKED"
    assert plan.recommended_price is None
    assert "trade_order" not in text
    assert "submit_order" not in text
