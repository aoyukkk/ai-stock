from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, TypeVar


INVALID_TIMING_CONTRACT = "INVALID_TIMING_CONTRACT"
T = TypeVar("T")


@dataclass(frozen=True)
class StrategyTimingContractSpec:
    stock_code: str
    trade_date: date
    observation_end_ts: datetime
    available_at_ts: datetime
    signal_generated_at: datetime
    order_eligible_at: datetime
    execution_policy: str
    feature_version: str
    data_snapshot_id: str
    universe_snapshot_id: str


def validate_timing_contract(contract: StrategyTimingContractSpec | object) -> None:
    """Fail closed before any forward-return or recommendation calculation."""

    values = (
        getattr(contract, "observation_end_ts", None),
        getattr(contract, "available_at_ts", None),
        getattr(contract, "signal_generated_at", None),
        getattr(contract, "order_eligible_at", None),
    )
    if any(value is None or not isinstance(value, datetime) for value in values):
        raise ValueError(INVALID_TIMING_CONTRACT)
    awareness = {value.tzinfo is not None and value.utcoffset() is not None for value in values}
    if len(awareness) != 1:
        raise ValueError(INVALID_TIMING_CONTRACT)
    observation_end_ts, available_at_ts, signal_generated_at, order_eligible_at = values
    if not observation_end_ts <= available_at_ts <= signal_generated_at < order_eligible_at:
        raise ValueError(INVALID_TIMING_CONTRACT)


def compute_forward_return(
    contract: StrategyTimingContractSpec | object,
    calculator: Callable[[], T],
) -> T:
    """Validate first; an invalid contract must never invoke the return calculator."""

    validate_timing_contract(contract)
    return calculator()
