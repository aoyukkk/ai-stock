from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from order_price.config import OrderPriceConfig
from order_price.schemas import OrderPriceInput


def calculate_vwap(context: OrderPriceInput) -> Decimal:
    if context.volume > 0 and context.amount > 0:
        return (context.amount / Decimal(context.volume)).quantize(Decimal("0.0001"))
    total_volume = sum(Decimal(bar.volume) for bar in context.kline_bars)
    if total_volume <= 0:
        return context.latest_price.quantize(Decimal("0.0001"))
    total_amount = sum((bar.amount for bar in context.kline_bars), Decimal("0"))
    return (total_amount / total_volume).quantize(Decimal("0.0001"))


def calculate_conservative_price(support: Decimal, atr: Decimal, config: OrderPriceConfig) -> Decimal:
    raw = support + config.conservative["support_atr_buffer"] * atr
    return round_to_tick(raw, config.tick_size)


def calculate_balanced_price(
    context: OrderPriceInput,
    support: Decimal,
    atr: Decimal,
    vwap: Decimal,
    config: OrderPriceConfig,
) -> Decimal:
    weights = config.balanced
    adjustment = Decimal("0")
    if context.recommendation == "STRONG_WATCH" or (
        context.recommendation == "WATCH" and context.committee_score >= Decimal("75")
    ):
        adjustment += weights["positive_adjustment_atr"] * atr
    if context.risk_level in {"HIGH", "BLACK_SWAN"}:
        adjustment += weights["negative_adjustment_atr"] * atr
    raw = (
        weights["support_weight"] * support
        + weights["vwap_weight"] * vwap
        + weights["previous_close_weight"] * context.previous_close
        + adjustment
    )
    return round_to_tick(raw, config.tick_size)


def calculate_aggressive_price(resistance: Decimal, config: OrderPriceConfig) -> Decimal:
    raw = resistance + config.aggressive["breakout_tick_buffer"]
    return round_to_tick(raw, config.tick_size)


def calculate_max_buy_price(context: OrderPriceInput, atr: Decimal, config: OrderPriceConfig) -> Decimal:
    max_config = config.max_buy_price
    candidates = [
        context.previous_close + max_config["atr_multiplier"] * atr,
        context.previous_close * (Decimal("1") + max_config["max_chase_percent"]),
    ]
    if bool(max_config.get("use_limit_up", True)):
        candidates.append(context.limit_up_price)
    return round_down_to_tick(min(candidates), config.tick_size)


def calculate_stop_loss(entry_price: Decimal, atr: Decimal, config: OrderPriceConfig) -> Decimal:
    stop_config = config.stop_loss
    raw = max(
        entry_price - stop_config["atr_multiplier"] * atr,
        entry_price * (Decimal("1") - stop_config["max_stop_loss_percent"]),
    )
    return round_to_tick(raw, config.tick_size)


def calculate_take_profit_prices(entry_price: Decimal, atr: Decimal, config: OrderPriceConfig) -> tuple[Decimal, Decimal]:
    take_profit = config.take_profit
    first = entry_price + take_profit["first_atr_multiplier"] * atr
    second = entry_price + take_profit["second_atr_multiplier"] * atr
    return round_to_tick(first, config.tick_size), round_to_tick(second, config.tick_size)


def calculate_risk_reward(entry_price: Decimal, stop_loss_price: Decimal, target_price: Decimal) -> Decimal:
    risk = entry_price - stop_loss_price
    reward = target_price - entry_price
    if risk <= 0 or reward <= 0:
        return Decimal("0.0000")
    return (reward / risk).quantize(Decimal("0.0001"))


def round_to_tick(value: Decimal, tick_size: Decimal) -> Decimal:
    return (value / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_size


def round_down_to_tick(value: Decimal, tick_size: Decimal) -> Decimal:
    return (value / tick_size).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick_size
