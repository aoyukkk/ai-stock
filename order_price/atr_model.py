from __future__ import annotations

from decimal import Decimal

from datasource.schemas import KlineBar
from order_price.exceptions import OrderPriceDataError


def calculate_true_range(bar: KlineBar) -> Decimal:
    high_low = bar.high - bar.low
    high_prev = abs(bar.high - bar.pre_close)
    low_prev = abs(bar.low - bar.pre_close)
    return max(high_low, high_prev, low_prev).quantize(Decimal("0.0001"))


def calculate_atr(kline_bars: list[KlineBar], window: int = 14) -> Decimal:
    if not kline_bars:
        raise OrderPriceDataError("Cannot calculate ATR without kline bars.")
    if window <= 0:
        raise OrderPriceDataError("ATR window must be greater than 0.")
    selected = kline_bars[-window:]
    total = sum((calculate_true_range(bar) for bar in selected), Decimal("0"))
    return (total / Decimal(len(selected))).quantize(Decimal("0.0001"))
