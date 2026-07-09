from decimal import Decimal

import pytest

from screening.exceptions import LightScreeningConfigError
from screening.schemas import LightScreeningLLMOutput
from screening.scoring import calculate_final_light_score, validate_light_screening_weights


WEIGHTS = {
    "quant_signal": Decimal("0.30"),
    "event_catalyst": Decimal("0.20"),
    "sector_strength": Decimal("0.15"),
    "order_friendliness": Decimal("0.15"),
    "liquidity": Decimal("0.10"),
    "risk_penalty": Decimal("0.10"),
}


def output(risk: Decimal, keep: bool = True) -> LightScreeningLLMOutput:
    return LightScreeningLLMOutput(
        stock_code="000001",
        opportunity_score=Decimal("80"),
        event_catalyst_score=Decimal("80"),
        sector_strength_score=Decimal("80"),
        order_friendliness_score=Decimal("80"),
        liquidity_score=Decimal("80"),
        risk_penalty_score=risk,
        confidence=Decimal("0.8"),
        direction="WATCH",
        reason="ok",
        risk_note="risk",
        should_keep=keep,
    )


def test_final_light_score_range_and_risk_penalty_lowers_score() -> None:
    low_risk = calculate_final_light_score(Decimal("80"), output(Decimal("10")), WEIGHTS)
    high_risk = calculate_final_light_score(Decimal("80"), output(Decimal("90")), WEIGHTS)

    assert Decimal("0") <= low_risk <= Decimal("100")
    assert high_risk < low_risk


def test_invalid_weights_raise() -> None:
    bad_weights = WEIGHTS | {"risk_penalty": Decimal("0.90")}

    with pytest.raises(LightScreeningConfigError):
        validate_light_screening_weights(bad_weights)


def test_should_keep_false_can_be_filtered_by_caller() -> None:
    items = [output(Decimal("10"), keep=True), output(Decimal("10"), keep=False)]
    kept = [item for item in items if item.should_keep]

    assert len(kept) == 1
