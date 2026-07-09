from datetime import date
from decimal import Decimal

import pytest

from datasource.schemas import KlineBar
from order_price.atr_model import calculate_atr, calculate_true_range
from order_price.exceptions import OrderPriceDataError


def make_bar(high: str, low: str, pre_close: str, close: str = "10.00") -> KlineBar:
    return KlineBar(
        stock_code="000001",
        trade_date=date(2026, 1, 1),
        open=Decimal("10.00"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        pre_close=Decimal(pre_close),
        volume=1000000,
        amount=Decimal("10000000"),
    )


def test_true_range_can_be_calculated() -> None:
    bar = make_bar("11.00", "9.50", "10.80")

    assert calculate_true_range(bar) == Decimal("1.5000")


def test_atr_can_be_calculated() -> None:
    bars = [
        make_bar("11.00", "10.00", "10.50"),
        make_bar("10.80", "9.80", "10.10"),
    ]

    assert calculate_atr(bars, window=14) == Decimal("1.0000")


def test_atr_uses_available_data_when_insufficient() -> None:
    assert calculate_atr([make_bar("10.50", "10.00", "10.20")], window=14) == Decimal("0.5000")


def test_atr_empty_data_raises_clear_error() -> None:
    with pytest.raises(OrderPriceDataError):
        calculate_atr([], window=14)
