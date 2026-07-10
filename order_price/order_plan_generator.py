from __future__ import annotations

from datetime import date
from decimal import Decimal

from order_price.atr_model import calculate_atr
from order_price.config import OrderPriceConfig
from order_price.execution_probability import estimate_fill_probability
from order_price.order_score import calculate_order_score
from order_price.price_level_calculator import (
    calculate_aggressive_price,
    calculate_balanced_price,
    calculate_conservative_price,
    calculate_max_buy_price,
    calculate_risk_reward,
    calculate_stop_loss,
    calculate_stop_loss_unrounded,
    calculate_take_profit_prices,
    calculate_vwap,
)
from order_price.schemas import OrderPlanDraft, OrderPriceInput, PriceLevelCandidate
from order_price.support_resistance import calculate_support_resistance


def generate_order_plan(context: OrderPriceInput, config: OrderPriceConfig, plan_date: date) -> OrderPlanDraft:
    config.validate()
    atr = calculate_atr(context.kline_bars, window=config.atr_window)
    support, resistance = calculate_support_resistance(context.kline_bars)
    vwap = calculate_vwap(context)
    max_acceptable_price = calculate_max_buy_price(context, atr, config)

    raw_levels: list[tuple[str, Decimal, str]] = []
    if config.price_levels["conservative"]:
        raw_levels.append((
            "CONSERVATIVE",
            calculate_conservative_price(support, atr, config),
            "Support plus configured ATR buffer.",
        ))
    if config.price_levels["balanced"]:
        raw_levels.append((
            "BALANCED",
            calculate_balanced_price(context, support, atr, vwap, config),
            "Weighted support, VWAP, previous close, and configured adjustment.",
        ))
    if config.price_levels["aggressive"]:
        raw_levels.append((
            "AGGRESSIVE",
            calculate_aggressive_price(resistance, config),
            "Resistance breakout plus configured tick buffer.",
        ))

    candidates = [
        _build_candidate(level_type, price, reason, context, config, support, resistance, atr, max_acceptable_price)
        for level_type, price, reason in raw_levels
    ]
    candidates.sort(key=lambda item: item.score, reverse=True)
    eligible = [candidate for candidate in candidates if candidate.price <= max_acceptable_price and candidate.score > 0]

    status = _plan_status(context)
    recommended = eligible[0] if eligible and status == "DRAFT" else None
    price_values = [candidate.price for candidate in candidates]
    stop_loss_price = recommended.stop_loss_price if recommended else None
    take_profit_1 = recommended.expected_profit_price if recommended else None
    take_profit_2 = None
    if recommended:
        _, take_profit_2 = calculate_take_profit_prices(recommended.price, atr, config)

    return OrderPlanDraft(
        stock_code=context.stock_code,
        stock_name=context.stock_name,
        plan_date=plan_date,
        plan_session="POST_MARKET",
        side=context.side,
        strategy_type=_strategy_type(recommended),
        recommended_price=recommended.price if recommended else None,
        price_range_low=min(price_values) if price_values else None,
        price_range_high=min(max(price_values), max_acceptable_price) if price_values else None,
        max_acceptable_price=max_acceptable_price,
        stop_loss_price=stop_loss_price,
        take_profit_1_price=take_profit_1,
        take_profit_2_price=take_profit_2,
        unrounded_stop_loss_price=recommended.unrounded_stop_loss_price if recommended else None,
        risk_reward_to_tp1=recommended.risk_reward_to_tp1 if recommended else None,
        risk_reward_to_tp2=recommended.risk_reward_to_tp2 if recommended else None,
        active_risk_reward=recommended.active_risk_reward if recommended else None,
        active_target_mode=config.risk_reward_target_mode,
        suggested_position_percent=config.default_position_percent,
        confidence=context.confidence,
        reason=_plan_reason(context, recommended, atr, support, resistance, vwap),
        valid_conditions={
            "support": str(support),
            "resistance": str(resistance),
            "atr": str(atr),
            "vwap": str(vwap),
            "max_acceptable_price": str(max_acceptable_price),
            "rule_engine": True,
        },
        cancel_conditions=_cancel_conditions(context, config, max_acceptable_price),
        reprice_conditions=_reprice_conditions(config),
        status=status,  # type: ignore[arg-type]
        candidates=candidates,
    )


def _build_candidate(
    price_type: str,
    price: Decimal,
    reason: str,
    context: OrderPriceInput,
    config: OrderPriceConfig,
    support: Decimal,
    resistance: Decimal,
    atr: Decimal,
    max_acceptable_price: Decimal,
) -> PriceLevelCandidate:
    unrounded_stop_loss_price = calculate_stop_loss_unrounded(price, atr, config)
    stop_loss_price = calculate_stop_loss(price, atr, config)
    take_profit_1, take_profit_2 = calculate_take_profit_prices(price, atr, config)
    risk_reward_to_tp1 = calculate_risk_reward(price, stop_loss_price, take_profit_1)
    risk_reward_to_tp2 = calculate_risk_reward(price, stop_loss_price, take_profit_2)
    active_targets = {
        "TAKE_PROFIT_1": risk_reward_to_tp1,
        "TAKE_PROFIT_2": risk_reward_to_tp2,
        "EXPECTED_PROFIT_PRICE": risk_reward_to_tp1,
    }
    risk_reward = active_targets[config.risk_reward_target_mode]
    expected_return = ((take_profit_1 - price) / price * Decimal("100")).quantize(Decimal("0.0001")) if price > 0 else Decimal("0")
    fill_probability = estimate_fill_probability(
        candidate_price=price,
        latest_price=context.latest_price,
        support=support,
        resistance=resistance,
        volume=context.volume,
        amount=context.amount,
        side=context.side,
    )
    candidate = PriceLevelCandidate(
        price_type=price_type,  # type: ignore[arg-type]
        price=price,
        score=Decimal("0"),
        fill_probability=fill_probability,
        expected_return=expected_return,
        risk_reward=risk_reward,
        expected_profit_price=take_profit_1,
        stop_loss_price=stop_loss_price,
        unrounded_stop_loss_price=unrounded_stop_loss_price,
        take_profit_1_price=take_profit_1,
        take_profit_2_price=take_profit_2,
        risk_reward_to_tp1=risk_reward_to_tp1,
        risk_reward_to_tp2=risk_reward_to_tp2,
        active_risk_reward=risk_reward,
        active_target_mode=config.risk_reward_target_mode,
        reason=reason,
    )
    score = calculate_order_score(candidate, context, config, max_acceptable_price)
    return candidate.model_copy(update={"score": score})


def _plan_status(context: OrderPriceInput) -> str:
    if context.risk_level == "BLACK_SWAN" or context.recommendation == "BLOCKED":
        return "BLOCKED"
    if context.risk_level == "HIGH":
        return "WATCH_ONLY"
    return "DRAFT"


def _strategy_type(candidate: PriceLevelCandidate | None) -> str:
    if candidate is None:
        return "RISK_BLOCKED"
    if candidate.price_type == "CONSERVATIVE":
        return "PULLBACK"
    if candidate.price_type == "AGGRESSIVE":
        return "BREAKOUT"
    return "BALANCED"


def _plan_reason(
    context: OrderPriceInput,
    recommended: PriceLevelCandidate | None,
    atr: Decimal,
    support: Decimal,
    resistance: Decimal,
    vwap: Decimal,
) -> str:
    if recommended is None:
        return (
            f"Rule engine did not select a tradable draft for {context.stock_code}; "
            f"risk_level={context.risk_level}, recommendation={context.recommendation}."
        )
    return (
        f"Rule-calculated {recommended.price_type} draft for {context.stock_code}; "
        f"ATR={atr}, support={support}, resistance={resistance}, VWAP={vwap}. "
        "This is not an automatic trading instruction."
    )


def _cancel_conditions(context: OrderPriceInput, config: OrderPriceConfig, max_acceptable_price: Decimal) -> dict:
    configured = config.cancel_conditions
    return {
        "high_open_percent": configured.get("high_open_percent"),
        "major_negative_news": configured.get("major_negative_news"),
        "risk_level_block": configured.get("risk_level_block"),
        "sector_heat_drop": configured.get("sector_heat_drop"),
        "current_price_above_max_acceptable": context.latest_price > max_acceptable_price,
    }


def _reprice_conditions(config: OrderPriceConfig) -> dict:
    configured = config.reprice_conditions
    return {
        "price_deviation_percent": configured.get("price_deviation_percent"),
        "auction_changed": configured.get("auction_changed"),
        "volatility_changed": configured.get("volatility_changed"),
        "vwap_or_support_changed": configured.get("vwap_or_support_changed", True),
    }
