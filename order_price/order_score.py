from __future__ import annotations

from decimal import Decimal

from order_price.config import OrderPriceConfig
from order_price.exceptions import OrderPriceConfigError
from order_price.schemas import OrderPriceInput, PriceLevelCandidate


def validate_order_score_weights(weights: dict[str, Decimal]) -> None:
    total = sum(weights.values(), Decimal("0"))
    if abs(total - Decimal("1")) > Decimal("0.0001"):
        raise OrderPriceConfigError(f"order_score_weights must sum to 1.0, got {total}.")


def calculate_order_score(
    candidate: PriceLevelCandidate,
    context: OrderPriceInput,
    config: OrderPriceConfig,
    max_acceptable_price: Decimal,
) -> Decimal:
    weights = config.order_score_weights
    validate_order_score_weights(weights)
    if candidate.price > max_acceptable_price:
        return Decimal("0.0000")
    if context.risk_level == "BLACK_SWAN":
        return Decimal("0.0000")

    expected_return_score = _clamp((candidate.expected_return / (config.max_chase_percent * Decimal("100"))) * Decimal("100"))
    fill_probability_score = candidate.fill_probability * Decimal("100")
    risk_reward_score = _clamp((candidate.risk_reward / config.ideal_risk_reward) * Decimal("100"))
    if candidate.risk_reward < config.min_risk_reward:
        risk_reward_score *= Decimal("0.50")
    emotion_score = context.emotion_score if context.emotion_score is not None else Decimal("50")
    capital_score = context.capital_score if context.capital_score is not None else Decimal("50")

    score = (
        expected_return_score * weights["expected_return"]
        + fill_probability_score * weights["fill_probability"]
        + risk_reward_score * weights["risk_reward"]
        + emotion_score * weights["emotion"]
        + capital_score * weights["capital_confirmation"]
    )
    return _clamp(score).quantize(Decimal("0.0001"))


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), value))
