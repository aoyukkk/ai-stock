import json
from decimal import Decimal

import pytest

from screening.exceptions import LightScreeningParseError
from screening.parser import parse_light_screening_output


def valid_payload(**overrides):
    item = {
        "stock_code": "000001",
        "opportunity_score": 80,
        "event_catalyst_score": 75,
        "sector_strength_score": 70,
        "order_friendliness_score": 65,
        "liquidity_score": 85,
        "risk_penalty_score": 20,
        "confidence": 0.8,
        "direction": "WATCH",
        "reason": "ok",
        "risk_note": "none",
        "should_keep": True,
        "data_conflict": False,
    }
    item.update(overrides)
    return json.dumps({"items": [item]})


def test_parse_valid_json() -> None:
    outputs = parse_light_screening_output(valid_payload())

    assert len(outputs) == 1
    assert outputs[0].stock_code == "000001"
    assert outputs[0].direction == "WATCH"


def test_scores_and_confidence_are_clamped_and_bad_direction_downgrades() -> None:
    output = parse_light_screening_output(
        valid_payload(
            opportunity_score=999,
            risk_penalty_score=-5,
            confidence=2,
            direction="MOON",
        )
    )[0]

    assert output.opportunity_score == Decimal("100.0000")
    assert output.risk_penalty_score == Decimal("0.0000")
    assert output.confidence == Decimal("1.0000")
    assert output.direction == "NEUTRAL"


def test_invalid_json_raises_clear_error() -> None:
    with pytest.raises(LightScreeningParseError):
        parse_light_screening_output("{not-json")


def test_missing_fields_raise_clear_error() -> None:
    with pytest.raises(LightScreeningParseError, match="Missing"):
        parse_light_screening_output(json.dumps({"items": [{"stock_code": "000001"}]}))
