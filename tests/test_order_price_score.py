from datetime import date
from decimal import Decimal

import pytest

from tests.test_order_price_calculator import make_context
from order_price.config import load_order_price_config
from order_price.order_score import calculate_order_score, validate_order_score_weights
from order_price.schemas import PriceLevelCandidate


def make_candidate(risk_reward: str = "2.0", fill_probability: str = "0.50") -> PriceLevelCandidate:
    return PriceLevelCandidate(
        price_type="BALANCED",
        price=Decimal("10.00"),
        score=Decimal("0"),
        fill_probability=Decimal(fill_probability),
        expected_return=Decimal("3.00"),
        risk_reward=Decimal(risk_reward),
        expected_profit_price=Decimal("10.30"),
        stop_loss_price=Decimal("9.85"),
        reason="test",
    )


def test_order_score_range_is_zero_to_one_hundred() -> None:
    score = calculate_order_score(
        make_candidate(),
        make_context(),
        load_order_price_config(),
        max_acceptable_price=Decimal("10.50"),
    )

    assert Decimal("0") <= score <= Decimal("100")


def test_higher_risk_reward_increases_score() -> None:
    config = load_order_price_config()
    context = make_context()

    low = calculate_order_score(make_candidate(risk_reward="0.8"), context, config, Decimal("10.50"))
    high = calculate_order_score(make_candidate(risk_reward="2.5"), context, config, Decimal("10.50"))

    assert high > low


def test_fill_probability_affects_score() -> None:
    config = load_order_price_config()
    context = make_context()

    low = calculate_order_score(make_candidate(fill_probability="0.20"), context, config, Decimal("10.50"))
    high = calculate_order_score(make_candidate(fill_probability="0.90"), context, config, Decimal("10.50"))

    assert high > low


def test_invalid_weights_raise_clear_error() -> None:
    with pytest.raises(Exception):
        validate_order_score_weights({"expected_return": Decimal("0.50")})
