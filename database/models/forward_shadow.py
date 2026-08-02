from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


VALUE = Numeric(18, 8)


class ForwardOutcome(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "forward_outcome"
    __table_args__ = (
        UniqueConstraint("source_run_id", "stock_code", "execution_policy", name="uq_forward_outcome_source_stock_policy"),
        Index("ix_forward_outcome_trade_status", "trade_date", "data_status"),
    )

    source_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    route: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    timing_contract_id: Mapped[int] = mapped_column(ForeignKey("strategy_timing_contract.id"), nullable=False)
    data_snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    universe_snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_status: Mapped[str | None] = mapped_column(String(32))
    v2_2_status: Mapped[str | None] = mapped_column(String(32))
    v3_status: Mapped[str | None] = mapped_column(String(32))
    factor_version: Mapped[str | None] = mapped_column(String(64))
    strategy_version: Mapped[str | None] = mapped_column(String(64))
    gate_version: Mapped[str | None] = mapped_column(String(64))
    execution_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_trade_date: Mapped[date | None] = mapped_column(Date)
    entry_price: Mapped[Decimal | None] = mapped_column(VALUE)
    entry_status: Mapped[str] = mapped_column(String(40), nullable=False)
    exit_trade_date_d1: Mapped[date | None] = mapped_column(Date)
    exit_price_d1: Mapped[Decimal | None] = mapped_column(VALUE)
    return_d1: Mapped[Decimal | None] = mapped_column(VALUE)
    exit_trade_date_d3: Mapped[date | None] = mapped_column(Date)
    exit_price_d3: Mapped[Decimal | None] = mapped_column(VALUE)
    return_d3: Mapped[Decimal | None] = mapped_column(VALUE)
    exit_trade_date_d5: Mapped[date | None] = mapped_column(Date)
    exit_price_d5: Mapped[Decimal | None] = mapped_column(VALUE)
    return_d5: Mapped[Decimal | None] = mapped_column(VALUE)
    exit_trade_date_d10: Mapped[date | None] = mapped_column(Date)
    exit_price_d10: Mapped[Decimal | None] = mapped_column(VALUE)
    return_d10: Mapped[Decimal | None] = mapped_column(VALUE)
    mae_d1: Mapped[Decimal | None] = mapped_column(VALUE)
    mfe_d1: Mapped[Decimal | None] = mapped_column(VALUE)
    mae_d3: Mapped[Decimal | None] = mapped_column(VALUE)
    mfe_d3: Mapped[Decimal | None] = mapped_column(VALUE)
    mae_d5: Mapped[Decimal | None] = mapped_column(VALUE)
    mfe_d5: Mapped[Decimal | None] = mapped_column(VALUE)
    benchmark_return: Mapped[Decimal | None] = mapped_column(VALUE)
    industry_return: Mapped[Decimal | None] = mapped_column(VALUE)
    data_status: Mapped[str] = mapped_column(String(40), nullable=False)
    horizon_status_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sensitivity_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    execution_details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    missed_opportunity: Mapped[Decimal | None] = mapped_column(VALUE)
    avoided_loss: Mapped[Decimal | None] = mapped_column(VALUE)
    non_fill_quality: Mapped[str | None] = mapped_column(String(32))
    all_triggered_gates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    binding_gate: Mapped[str | None] = mapped_column(String(64))
    co_binding_gates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evaluation_order: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    unique_block_reason: Mapped[str | None] = mapped_column(String(256))
    joint_block_reason: Mapped[str | None] = mapped_column(String(512))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


class GateValueEvaluation(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "gate_value_evaluation"
    __table_args__ = (UniqueConstraint("input_hash", "gate_name", "evaluation_horizon", name="uq_gate_value_input_gate_horizon"),)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    gate_name: Mapped[str] = mapped_column(String(64), nullable=False)
    gate_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    reached_count: Mapped[int] = mapped_column(nullable=False)
    triggered_count: Mapped[int] = mapped_column(nullable=False)
    blocked_count: Mapped[int] = mapped_column(nullable=False)
    unique_blocked_count: Mapped[int] = mapped_column(nullable=False)
    co_blocked_count: Mapped[int] = mapped_column(nullable=False)
    binding_count: Mapped[int] = mapped_column(nullable=False)
    pass_flip_count: Mapped[int] = mapped_column(nullable=False)
    top20_flip_count: Mapped[int] = mapped_column(nullable=False)
    mean_rank_delta: Mapped[Decimal | None] = mapped_column(VALUE)
    mean_score_delta: Mapped[Decimal | None] = mapped_column(VALUE)
    distance_to_threshold: Mapped[Decimal | None] = mapped_column(VALUE)
    avoided_loss: Mapped[Decimal | None] = mapped_column(VALUE)
    missed_gain: Mapped[Decimal | None] = mapped_column(VALUE)
    local_net_gate_value: Mapped[Decimal | None] = mapped_column(VALUE)
    portfolio_marginal_value: Mapped[Decimal | None] = mapped_column(VALUE)
    false_negative_rate: Mapped[Decimal | None] = mapped_column(VALUE)
    reject_precision: Mapped[Decimal | None] = mapped_column(VALUE)
    evaluation_horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    sample_status: Mapped[str] = mapped_column(String(32), nullable=False)
    overlap_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


class GateCounterfactualRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "gate_counterfactual_run"
    __table_args__ = (UniqueConstraint("input_hash", name="uq_gate_counterfactual_input"),)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    disabled_gate: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    delta_net_return: Mapped[Decimal | None] = mapped_column(VALUE)
    delta_win_rate: Mapped[Decimal | None] = mapped_column(VALUE)
    delta_profit_factor: Mapped[Decimal | None] = mapped_column(VALUE)
    delta_max_drawdown: Mapped[Decimal | None] = mapped_column(VALUE)
    delta_cvar: Mapped[Decimal | None] = mapped_column(VALUE)
    delta_candidate_count: Mapped[int] = mapped_column(nullable=False, default=0)
    delta_turnover: Mapped[Decimal | None] = mapped_column(VALUE)
    delta_top20_membership: Mapped[int] = mapped_column(nullable=False, default=0)
    avoided_loss: Mapped[Decimal | None] = mapped_column(VALUE)
    missed_gain: Mapped[Decimal | None] = mapped_column(VALUE)
    net_gate_value: Mapped[Decimal | None] = mapped_column(VALUE)
    replay_status: Mapped[str] = mapped_column(String(32), nullable=False)
    shapley_ready_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


class ModelVersionComparison(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_version_comparison"
    __table_args__ = (UniqueConstraint("input_hash", name="uq_model_version_comparison_input"),)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    model_route: Mapped[str] = mapped_column(String(64), nullable=False)
    segment: Mapped[str] = mapped_column(String(96), nullable=False)
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    sample_count: Mapped[int] = mapped_column(nullable=False)
    fill_count: Mapped[int] = mapped_column(nullable=False)
    positive_rate: Mapped[Decimal | None] = mapped_column(VALUE)
    average_return: Mapped[Decimal | None] = mapped_column(VALUE)
    median_return: Mapped[Decimal | None] = mapped_column(VALUE)
    average_mae: Mapped[Decimal | None] = mapped_column(VALUE)
    average_mfe: Mapped[Decimal | None] = mapped_column(VALUE)
    profit_factor: Mapped[Decimal | None] = mapped_column(VALUE)
    maximum_loss: Mapped[Decimal | None] = mapped_column(VALUE)
    sample_status: Mapped[str] = mapped_column(String(32), nullable=False)
    fair_sample: Mapped[bool] = mapped_column(nullable=False, default=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


def _immutable(*_args, **_kwargs) -> None:
    raise ValueError("IMMUTABLE_FORWARD_SHADOW_EVALUATION")


for _model in (GateValueEvaluation, GateCounterfactualRun, ModelVersionComparison):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
