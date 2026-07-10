from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SizingStatus(StrEnum):
    SUGGESTED = "SUGGESTED"
    ZERO_ALLOCATION = "ZERO_ALLOCATION"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class PositionSizingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advisory_only: bool = True
    conviction_power: Decimal = Field(default=Decimal("2"), gt=0, le=5)
    portfolio_open_risk_percent: Decimal = Field(default=Decimal("0.01"), ge=0, le=Decimal("0.10"))
    deployable_capital_percent: Decimal = Field(default=Decimal("0.80"), ge=0, le=1)
    cash_reserve_percent: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    max_single_stock_percent: Decimal = Field(default=Decimal("0.15"), ge=0, le=1)
    max_industry_percent: Decimal = Field(default=Decimal("0.30"), ge=0, le=1)
    max_chain_percent: Decimal = Field(default=Decimal("0.35"), ge=0, le=1)
    max_liquidity_participation: Decimal = Field(default=Decimal("0.01"), ge=0, le=1)
    default_lot_size: int = Field(default=100, ge=1)
    max_stop_loss_percent: Decimal = Field(default=Decimal("0.08"), ge=0, le=1)
    minimum_risk_reward: Decimal = Field(default=Decimal("1.5"), ge=0, le=20)
    unverified_research_discount: Decimal = Field(default=Decimal("0.80"), ge=0, le=1)

    @model_validator(mode="after")
    def validate_capital_partition(self):
        if self.deployable_capital_percent + self.cash_reserve_percent != Decimal("1"):
            raise ValueError("deployable_capital_percent + cash_reserve_percent must equal 1")
        if not self.advisory_only:
            raise ValueError("position sizing must remain advisory-only")
        return self


class AccountState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equity: Decimal = Field(gt=0)
    available_cash: Decimal = Field(ge=0)
    stock_exposure: dict[str, Decimal] = Field(default_factory=dict)
    industry_exposure: dict[str, Decimal] = Field(default_factory=dict)
    chain_exposure: dict[str, Decimal] = Field(default_factory=dict)


class SizingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str
    final_score: Decimal = Field(ge=0, le=100)
    controller_confidence: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    data_quality_factor: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    risk_gate_factor: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    entry_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, ge=0)
    max_acceptable_price: Decimal | None = Field(default=None, gt=0)
    risk_reward: Decimal | None = Field(default=None, ge=0)
    atr: Decimal | None = Field(default=None, ge=0)
    average_daily_amount: Decimal = Field(default=Decimal("0"), ge=0)
    industry: str = "unknown"
    industry_chain: str = "unknown"
    lot_size: int = Field(default=100, ge=1)
    risk_level: str = "NORMAL"
    blocked: bool = False
    data_conflict: bool = False
    unverified_fundamental_research: bool = False


class PositionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str
    status: SizingStatus
    relative_allocation_weight: Decimal
    account_position_percent: Decimal
    suggested_capital: Decimal
    suggested_quantity: int
    risk_per_share: Decimal
    maximum_planned_loss: Decimal
    binding_constraint: str
    constraint_quantities: dict[str, int]
    warnings: list[str] = Field(default_factory=list)


class AllocationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "position-sizing-v1"
    advisory_only: bool = True
    suggestions: list[PositionSuggestion]
    total_suggested_capital: Decimal
    total_maximum_planned_loss: Decimal
