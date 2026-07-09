from __future__ import annotations

from decimal import Decimal

from datasource.schemas import KlineBar
from order_price.exceptions import OrderPriceDataError


def calculate_support_resistance(kline_bars: list[KlineBar], lookback: int = 20) -> tuple[Decimal, Decimal]:
    if not kline_bars:
        raise OrderPriceDataError("Cannot calculate support/resistance without kline bars.")
    selected = kline_bars[-max(1, lookback):]
    support = min(bar.low for bar in selected).quantize(Decimal("0.0001"))
    resistance = max(bar.high for bar in selected).quantize(Decimal("0.0001"))
    if support > resistance:
        raise OrderPriceDataError("Calculated support is above resistance.")
    return support, resistance
