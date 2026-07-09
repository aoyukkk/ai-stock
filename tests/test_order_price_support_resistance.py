from datetime import date, timedelta
from decimal import Decimal

import pytest

from datasource.schemas import KlineBar
from order_price.exceptions import OrderPriceDataError
from order_price.support_resistance import calculate_support_resistance


def make_bars(count: int = 5) -> list[KlineBar]:
    start = date(2026, 1, 1)
    bars = []
    for index in range(count):
        price = Decimal("10") + Decimal(index) / Decimal("10")
        bars.append(
            KlineBar(
                stock_code="000001",
                trade_date=start + timedelta(days=index),
                open=price,
                high=price + Decimal("0.50"),
                low=price - Decimal("0.30"),
                close=price + Decimal("0.10"),
                pre_close=price - Decimal("0.05"),
                volume=1000000,
                amount=price * Decimal("1000000"),
            )
        )
    return bars


def test_support_resistance_can_be_calculated() -> None:
    support, resistance = calculate_support_resistance(make_bars())

    assert support <= resistance
    assert support == Decimal("9.7000")
    assert resistance == Decimal("10.9000")


def test_support_resistance_short_data_is_usable() -> None:
    support, resistance = calculate_support_resistance(make_bars(1))

    assert support == Decimal("9.7000")
    assert resistance == Decimal("10.5000")


def test_support_resistance_empty_data_raises_clear_error() -> None:
    with pytest.raises(OrderPriceDataError):
        calculate_support_resistance([])
