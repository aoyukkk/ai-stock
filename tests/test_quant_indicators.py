from datetime import date
from decimal import Decimal

from datasource.schemas import KlineBar
from quant.indicators import (
    atr,
    max_drawdown,
    moving_average,
    percentage_return,
    rolling_return,
    rsi,
    simple_vwap,
    true_range,
    volatility,
)


def make_bar(day: int, close: Decimal) -> KlineBar:
    return KlineBar(
        stock_code="000001",
        trade_date=date(2026, 1, day),
        open=close - Decimal("0.10"),
        high=close + Decimal("0.20"),
        low=close - Decimal("0.20"),
        close=close,
        pre_close=close - Decimal("0.05"),
        volume=1000 + day,
        amount=close * Decimal(1000 + day),
        frequency="1d",
    )


def test_indicators_calculate_normal_inputs() -> None:
    values = [Decimal(i) for i in range(1, 31)]
    bars = [make_bar(i, Decimal("10") + Decimal(i) / Decimal("10")) for i in range(1, 31)]

    assert moving_average(values, 5) == Decimal("28.0000")
    assert percentage_return(Decimal("11"), Decimal("10")) == Decimal("10.0000")
    assert rolling_return(values, 5) is not None
    assert volatility(values, 20) is not None
    assert max_drawdown([10, 12, 11, 9, 13], 5) == Decimal("25.0000")
    assert true_range(Decimal("11"), Decimal("10"), Decimal("10.5")) == Decimal("1.0000")
    assert atr(bars, 14) is not None
    assert rsi(values, 14) == Decimal("100.0000")
    assert simple_vwap(bars) is not None


def test_indicators_short_or_empty_inputs_are_safe() -> None:
    assert moving_average([], 5) is None
    assert rolling_return([1, 2], 5) is None
    assert volatility([1], 20) is None
    assert max_drawdown([1], 20) is None
    assert atr([], 14) is None
    assert rsi([1, 2], 14) is None
    assert simple_vwap([]) is None
