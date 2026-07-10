from __future__ import annotations

from decimal import Decimal
from statistics import mean, pstdev


def _to_decimal_list(values) -> list[Decimal]:
    return [Decimal(str(value)) for value in values if value is not None]


def moving_average(values, window: int) -> Decimal | None:
    clean = _to_decimal_list(values)
    if window <= 0 or len(clean) < window:
        return None
    return Decimal(str(mean(clean[-window:]))).quantize(Decimal("0.0001"))


def exponential_moving_average(values, window: int) -> Decimal | None:
    clean = _to_decimal_list(values)
    if window <= 0 or len(clean) < window:
        return None
    multiplier = Decimal("2") / Decimal(window + 1)
    result = Decimal(str(mean(clean[:window])))
    for value in clean[window:]:
        result = (value - result) * multiplier + result
    return result.quantize(Decimal("0.0001"))


def macd(values, fast: int = 12, slow: int = 26) -> tuple[Decimal | None, Decimal | None]:
    fast_value = exponential_moving_average(values, fast)
    slow_value = exponential_moving_average(values, slow)
    if fast_value is None or slow_value is None:
        return None, None
    return fast_value, slow_value


def percentage_return(current, previous) -> Decimal | None:
    if current is None or previous in (None, 0):
        return None
    current_decimal = Decimal(str(current))
    previous_decimal = Decimal(str(previous))
    if previous_decimal == 0:
        return None
    return ((current_decimal - previous_decimal) / previous_decimal * Decimal("100")).quantize(Decimal("0.0001"))


def rolling_return(close_prices, window: int) -> Decimal | None:
    clean = _to_decimal_list(close_prices)
    if window <= 0 or len(clean) <= window:
        return None
    return percentage_return(clean[-1], clean[-window - 1])


def volatility(close_prices, window: int) -> Decimal | None:
    clean = _to_decimal_list(close_prices)
    if window <= 1 or len(clean) < window:
        return None
    returns: list[Decimal] = []
    window_values = clean[-window:]
    for previous, current in zip(window_values, window_values[1:]):
        item_return = percentage_return(current, previous)
        if item_return is not None:
            returns.append(item_return)
    if len(returns) < 2:
        return None
    return Decimal(str(pstdev(returns))).quantize(Decimal("0.0001"))


def max_drawdown(close_prices, window: int) -> Decimal | None:
    clean = _to_decimal_list(close_prices)
    if window <= 1 or len(clean) < window:
        return None
    window_values = clean[-window:]
    peak = window_values[0]
    max_dd = Decimal("0")
    for value in window_values:
        peak = max(peak, value)
        if peak != 0:
            drawdown = (peak - value) / peak * Decimal("100")
            max_dd = max(max_dd, drawdown)
    return max_dd.quantize(Decimal("0.0001"))


def true_range(high, low, previous_close) -> Decimal | None:
    if high is None or low is None or previous_close is None:
        return None
    high_decimal = Decimal(str(high))
    low_decimal = Decimal(str(low))
    previous_close_decimal = Decimal(str(previous_close))
    return max(
        high_decimal - low_decimal,
        abs(high_decimal - previous_close_decimal),
        abs(low_decimal - previous_close_decimal),
    ).quantize(Decimal("0.0001"))


def atr(bars, window: int) -> Decimal | None:
    if window <= 0 or len(bars) < window:
        return None
    ranges = [
        true_range(bar.high, bar.low, bar.pre_close)
        for bar in bars[-window:]
    ]
    clean_ranges = [item for item in ranges if item is not None]
    if len(clean_ranges) < window:
        return None
    return Decimal(str(mean(clean_ranges))).quantize(Decimal("0.0001"))


def rsi(close_prices, window: int) -> Decimal | None:
    clean = _to_decimal_list(close_prices)
    if window <= 0 or len(clean) <= window:
        return None
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    window_values = clean[-window - 1:]
    for previous, current in zip(window_values, window_values[1:]):
        change = current - previous
        if change >= 0:
            gains.append(change)
            losses.append(Decimal("0"))
        else:
            gains.append(Decimal("0"))
            losses.append(abs(change))
    avg_gain = Decimal(str(mean(gains)))
    avg_loss = Decimal(str(mean(losses)))
    if avg_loss == 0:
        return Decimal("100.0000")
    rs = avg_gain / avg_loss
    return (Decimal("100") - (Decimal("100") / (Decimal("1") + rs))).quantize(Decimal("0.0001"))


def simple_vwap(bars) -> Decimal | None:
    if not bars:
        return None
    total_volume = Decimal("0")
    total_value = Decimal("0")
    for bar in bars:
        volume = Decimal(str(bar.volume or 0))
        typical_price = (Decimal(str(bar.high)) + Decimal(str(bar.low)) + Decimal(str(bar.close))) / Decimal("3")
        total_volume += volume
        total_value += typical_price * volume
    if total_volume == 0:
        return None
    return (total_value / total_volume).quantize(Decimal("0.0001"))
