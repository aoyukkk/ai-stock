from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


SCORE = Numeric(14, 6)


class StrategyTimingContract(IDMixin, TimestampMixin, ReprMixin, Base):
    """Append-only point-in-time contract for a shadow recommendation."""

    __tablename__ = "strategy_timing_contract"
    __table_args__ = (
        CheckConstraint(
            "observation_end_ts <= available_at_ts",
            name="ck_strategy_timing_observation_available",
        ),
        CheckConstraint(
            "available_at_ts <= signal_generated_at",
            name="ck_strategy_timing_available_signal",
        ),
        CheckConstraint(
            "signal_generated_at < order_eligible_at",
            name="ck_strategy_timing_signal_order",
        ),
        UniqueConstraint(
            "stock_code",
            "trade_date",
            "feature_version",
            "data_snapshot_id",
            "universe_snapshot_id",
            name="uq_strategy_timing_contract_snapshot",
        ),
        Index("ix_strategy_timing_trade_stock", "trade_date", "stock_code"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    observation_end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal_generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    order_eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    data_snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    universe_snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)


class AdmissionV3Run(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "admission_v3_run"
    __table_args__ = (
        Index("ix_admission_v3_trade_status", "trade_date", "status"),
        Index("ix_admission_v3_input_hash", "input_hash", unique=True),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_v2_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_count: Mapped[int] = mapped_column(nullable=False)
    pass_core_count: Mapped[int] = mapped_column(nullable=False)
    pass_exploratory_count: Mapped[int] = mapped_column(nullable=False)
    review_count: Mapped[int] = mapped_column(nullable=False)
    reject_count: Mapped[int] = mapped_column(nullable=False)
    open_set_count: Mapped[int] = mapped_column(nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    shadow_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled_in_production: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quant_hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_hash_after: Mapped[str] = mapped_column(String(64), nullable=False)
    flash_hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    flash_hash_after: Mapped[str] = mapped_column(String(64), nullable=False)
    pro_hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    pro_hash_after: Mapped[str] = mapped_column(String(64), nullable=False)
    llm_call_count: Mapped[int] = mapped_column(nullable=False, default=0)
    external_api_call_count: Mapped[int] = mapped_column(nullable=False, default=0)
    order_creation_count: Mapped[int] = mapped_column(nullable=False, default=0)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


class FactorAttribution(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "factor_attribution"
    __table_args__ = (
        UniqueConstraint("run_id", "stock_code", "factor_family", name="uq_factor_attribution_run_stock_family"),
        Index("ix_factor_attribution_trade_family", "trade_date", "factor_family"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    timing_contract_id: Mapped[int] = mapped_column(ForeignKey("strategy_timing_contract.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    factor_family: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_signal: Mapped[dict] = mapped_column(JSON, nullable=False)
    normalized_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    score_contribution: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    gate_contribution: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    rank_contribution: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    interaction_note: Mapped[str] = mapped_column(String(512), nullable=False)
    lineage_json: Mapped[list] = mapped_column(JSON, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


class AdmissionV3Result(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "admission_v3_result"
    __table_args__ = (
        UniqueConstraint("run_id", "stock_code", name="uq_admission_v3_run_stock"),
        Index("ix_admission_v3_trade_state", "trade_date", "admission_state"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    timing_contract_id: Mapped[int] = mapped_column(ForeignKey("strategy_timing_contract.id"), nullable=False)
    source_v2_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(128))
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    quant_rank: Mapped[int | None] = mapped_column()
    industry: Mapped[str | None] = mapped_column(String(128))
    admission_state: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_status: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_probability: Mapped[dict] = mapped_column(JSON, nullable=False)
    hard_gate_results: Mapped[dict] = mapped_column(JSON, nullable=False)
    risk_penalties: Mapped[dict] = mapped_column(JSON, nullable=False)
    opportunity_components: Mapped[dict] = mapped_column(JSON, nullable=False)
    portfolio_adjustments: Mapped[dict] = mapped_column(JSON, nullable=False)
    counterfactuals: Mapped[dict] = mapped_column(JSON, nullable=False)
    base_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    opportunity_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    final_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    expected_value_score: Mapped[Decimal | None] = mapped_column(SCORE)
    risk_adjusted_opportunity_score: Mapped[Decimal | None] = mapped_column(SCORE)
    position_multiplier: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    selected_reason: Mapped[str | None] = mapped_column(String(512))
    rejected_reasons: Mapped[list] = mapped_column(JSON, nullable=False)
    largest_factor: Mapped[str | None] = mapped_column(String(32))
    largest_gate: Mapped[str | None] = mapped_column(String(64))
    llm_structured_output: Mapped[dict] = mapped_column(JSON, nullable=False)
    final_llm_score: Mapped[Decimal | None] = mapped_column(SCORE)
    shadow_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


class GateEvaluation(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "gate_evaluation"
    __table_args__ = (
        UniqueConstraint("run_id", "gate_name", name="uq_gate_evaluation_run_gate"),
        Index("ix_gate_evaluation_trade_gate", "trade_date", "gate_name"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    gate_name: Mapped[str] = mapped_column(String(64), nullable=False)
    blocked_count: Mapped[int] = mapped_column(nullable=False)
    evaluated_count: Mapped[int] = mapped_column(nullable=False)
    future_return: Mapped[Decimal | None] = mapped_column(SCORE)
    avoided_loss: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    missed_gain: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    net_gate_value: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    counterfactual_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


class FactorPerformanceHistory(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "factor_performance_history"
    __table_args__ = (
        UniqueConstraint("input_hash", "factor_family", name="uq_factor_performance_input_family"),
        Index("ix_factor_performance_period_family", "period_end", "factor_family"),
    )

    factor_family: Mapped[str] = mapped_column(String(32), nullable=False)
    sample_count: Mapped[int] = mapped_column(nullable=False)
    win_rate: Mapped[Decimal | None] = mapped_column(SCORE)
    avg_return_d1: Mapped[Decimal | None] = mapped_column(SCORE)
    avg_return_d3: Mapped[Decimal | None] = mapped_column(SCORE)
    avg_return_d5: Mapped[Decimal | None] = mapped_column(SCORE)
    avg_drawdown: Mapped[Decimal | None] = mapped_column(SCORE)
    positive_contribution_rate: Mapped[Decimal | None] = mapped_column(SCORE)
    negative_contribution_rate: Mapped[Decimal | None] = mapped_column(SCORE)
    period: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


def _validate_timing_contract(_mapper, _connection, target: StrategyTimingContract) -> None:
    values = (
        target.observation_end_ts,
        target.available_at_ts,
        target.signal_generated_at,
        target.order_eligible_at,
    )
    if any(value is None for value in values):
        raise ValueError("INVALID_TIMING_CONTRACT")
    if not (
        target.observation_end_ts <= target.available_at_ts
        <= target.signal_generated_at
        < target.order_eligible_at
    ):
        raise ValueError("INVALID_TIMING_CONTRACT")


def _immutable(*_args, **_kwargs) -> None:
    raise ValueError("IMMUTABLE_DECISION_EXPLAINABILITY_SNAPSHOT")


event.listen(StrategyTimingContract, "before_insert", _validate_timing_contract)
for _model in (
    StrategyTimingContract,
    AdmissionV3Run,
    FactorAttribution,
    AdmissionV3Result,
    GateEvaluation,
    FactorPerformanceHistory,
):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
