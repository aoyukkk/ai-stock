from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from position_sizing.engine import PositionSizingEngine
from position_sizing.schemas import AccountState, PositionSizingConfig, SizingCandidate, SizingStatus


def _account() -> AccountState:
    return AccountState(equity=Decimal("1000000"), available_cash=Decimal("800000"))


def _candidate(code: str = "000001.SZ", score: str = "80") -> SizingCandidate:
    return SizingCandidate(
        stock_code=code,
        final_score=Decimal(score),
        controller_confidence=Decimal("0.9"),
        data_quality_factor=Decimal("0.8"),
        risk_gate_factor=Decimal("1"),
        entry_price=Decimal("10"),
        stop_price=Decimal("9.2"),
        average_daily_amount=Decimal("100000000"),
        industry="bank",
        industry_chain="finance",
    )


def test_relative_weights_are_normalized_and_distinct_from_account_positions():
    result = PositionSizingEngine().evaluate(_account(), [_candidate(), _candidate("600000.SH", "60")])
    assert sum(item.relative_allocation_weight for item in result.suggestions) == Decimal("1")
    assert sum(item.account_position_percent for item in result.suggestions) < Decimal("1")
    assert all(item.suggested_quantity % 100 == 0 for item in result.suggestions)


def test_risk_budget_and_caps_are_never_exceeded():
    result = PositionSizingEngine().evaluate(_account(), [_candidate()])
    item = result.suggestions[0]
    assert item.maximum_planned_loss <= Decimal("10000")
    assert item.suggested_capital <= Decimal("150000")
    assert item.status is SizingStatus.SUGGESTED
    assert item.suggested_quantity == min(item.constraint_quantities.values())


def test_invalid_stop_fails_closed_for_manual_review():
    candidate = _candidate()
    candidate.stop_price = Decimal("10")
    item = PositionSizingEngine().evaluate(_account(), [candidate]).suggestions[0]
    assert item.status is SizingStatus.NEEDS_REVIEW
    assert item.suggested_quantity == 0
    assert item.maximum_planned_loss == 0


def test_ten_percent_stop_distance_requires_review():
    candidate = _candidate()
    candidate.stop_price = Decimal("9")
    item = PositionSizingEngine().evaluate(_account(), [candidate]).suggestions[0]
    assert item.status is SizingStatus.NEEDS_REVIEW
    assert item.suggested_quantity == 0
    assert "UPSTREAM_STOP_LOSS_CONFLICT" in item.warnings


def test_zero_risk_gate_produces_zero_allocation():
    candidate = _candidate()
    candidate.risk_gate_factor = Decimal("0")
    item = PositionSizingEngine().evaluate(_account(), [candidate]).suggestions[0]
    assert item.status is SizingStatus.ZERO_ALLOCATION
    assert item.suggested_quantity == 0


def test_config_is_advisory_only_and_capital_partition_is_exact():
    with pytest.raises(ValidationError):
        PositionSizingConfig(advisory_only=False)
    with pytest.raises(ValidationError):
        PositionSizingConfig(deployable_capital_percent=Decimal("0.7"))


@pytest.mark.parametrize(
    ("changes", "warning"),
    [
        ({"max_acceptable_price": Decimal("9.9")}, "ENTRY_ABOVE_MAX_ACCEPTABLE_PRICE"),
        ({"risk_reward": Decimal("1.0")}, "RISK_REWARD_BELOW_MINIMUM"),
        ({"blocked": True}, "UPSTREAM_STOP_LOSS_CONFLICT"),
        ({"risk_level": "BLACK_SWAN"}, "UPSTREAM_STOP_LOSS_CONFLICT"),
        ({"data_conflict": True}, "DATA_CONFLICT"),
    ],
)
def test_upstream_conflicts_zero_quantity(changes, warning):
    candidate = _candidate()
    for key, value in changes.items():
        setattr(candidate, key, value)
    item = PositionSizingEngine().evaluate(_account(), [candidate]).suggestions[0]
    assert item.status is SizingStatus.NEEDS_REVIEW
    assert item.suggested_quantity == 0
    assert warning in item.warnings


def test_unverified_research_can_only_discount_position():
    verified = PositionSizingEngine().evaluate(_account(), [_candidate(), _candidate("600000.SH")])
    unverified_candidate = _candidate()
    unverified_candidate.unverified_fundamental_research = True
    discounted = PositionSizingEngine().evaluate(
        _account(), [unverified_candidate, _candidate("600000.SH")]
    )
    assert discounted.suggestions[0].relative_allocation_weight < verified.suggestions[0].relative_allocation_weight
    assert "UNVERIFIED_FUNDAMENTAL_RESEARCH" in discounted.suggestions[0].warnings
