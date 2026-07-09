from __future__ import annotations

from decimal import Decimal

from screening.exceptions import LightScreeningConfigError
from screening.schemas import LightScreeningLLMOutput


def validate_light_screening_weights(weights: dict[str, Decimal]) -> None:
    total = sum(weights.values(), Decimal("0"))
    if abs(total - Decimal("1")) > Decimal("0.0001"):
        raise LightScreeningConfigError(
            f"Light screening score weights must sum to 1.0, got {total}."
        )


def calculate_final_light_score(
    quant_score,
    llm_output: LightScreeningLLMOutput,
    weights: dict[str, Decimal],
) -> Decimal:
    validate_light_screening_weights(weights)
    quant_signal = Decimal(str(quant_score))
    score = (
        quant_signal * weights["quant_signal"]
        + llm_output.event_catalyst_score * weights["event_catalyst"]
        + llm_output.sector_strength_score * weights["sector_strength"]
        + llm_output.order_friendliness_score * weights["order_friendliness"]
        + llm_output.liquidity_score * weights["liquidity"]
        - llm_output.risk_penalty_score * weights["risk_penalty"]
    )
    return _clamp(score)


def calculate_llm_score(llm_output: LightScreeningLLMOutput) -> Decimal:
    score = (
        llm_output.opportunity_score
        + llm_output.event_catalyst_score
        + llm_output.sector_strength_score
        + llm_output.order_friendliness_score
        + llm_output.liquidity_score
        - llm_output.risk_penalty_score
    ) / Decimal("5")
    return _clamp(score)


def _clamp(value) -> Decimal:
    numeric = Decimal(str(value))
    return max(Decimal("0"), min(Decimal("100"), numeric)).quantize(Decimal("0.0001"))
