from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


PRICE = Numeric(14, 4)


class ModelValidationRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_validation_run"
    __table_args__ = (Index("ix_model_validation_run_run_id", "run_id", unique=True),)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_data_manifest_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    knowledge_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_market_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    real_llm: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    expected_universe_audit: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class ModelValidationSample(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_validation_sample"
    __table_args__ = (Index("ix_model_validation_sample_run_rank", "validation_run_id", "rank", unique=True),)
    validation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_data_manifest_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rank: Mapped[int] = mapped_column(nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(128), nullable=False)
    quant_scores: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    latest_financial_period: Mapped[str | None] = mapped_column(String(16))
    financial_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_age_days: Mapped[int | None] = mapped_column()
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fundamental_result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    screening_result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    field_provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class ModelValidationLLMAudit(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_validation_llm_audit"
    __table_args__ = (Index("ix_model_validation_llm_run_stock", "validation_run_id", "stock_code"),)
    validation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    model_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    actual_model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    latency_ms: Mapped[int] = mapped_column(default=0, nullable=False)
    cache_status: Mapped[str] = mapped_column(String(16), nullable=False)


class ModelValidationOrderPlan(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_validation_order_plan"
    __table_args__ = (Index("ix_model_validation_order_run_stock", "validation_run_id", "stock_code", unique=True),)
    validation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_data_manifest_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_purpose: Mapped[str] = mapped_column(String(32), default="MODEL_VALIDATION", nullable=False)
    plan_session: Mapped[str] = mapped_column(String(32), default="POST_MARKET", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    actionable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_final_recommendation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    decision_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_market_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    factor_version: Mapped[str | None] = mapped_column(String(64))
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    conservative_price: Mapped[Decimal | None] = mapped_column(PRICE)
    balanced_price: Mapped[Decimal | None] = mapped_column(PRICE)
    aggressive_price: Mapped[Decimal | None] = mapped_column(PRICE)
    recommended_price: Mapped[Decimal | None] = mapped_column(PRICE)
    max_acceptable_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_loss_price: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit_1_price: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit_2_price: Mapped[Decimal | None] = mapped_column(PRICE)
    fill_probability: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    risk_reward: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    order_price_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    support: Mapped[Decimal | None] = mapped_column(PRICE)
    resistance: Mapped[Decimal | None] = mapped_column(PRICE)
    atr: Mapped[Decimal | None] = mapped_column(PRICE)
    vwap: Mapped[Decimal | None] = mapped_column(PRICE)
    previous_close: Mapped[Decimal | None] = mapped_column(PRICE)
    limit_up_estimated: Mapped[Decimal | None] = mapped_column(PRICE)
    limit_down_estimated: Mapped[Decimal | None] = mapped_column(PRICE)
    limit_price_source: Mapped[str] = mapped_column(String(32), default="RULE_ESTIMATED", nullable=False)
    official_target_day_limit_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    target_day_auction_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancel_conditions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reprice_conditions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    temporal_status: Mapped[str] = mapped_column(String(32), nullable=False)


class ValidationAccountSnapshot(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "validation_account_snapshot"
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    validation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), default="MODEL_VALIDATION", nullable=False)
    account_equity: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    available_cash: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    existing_positions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class ModelValidationAllocation(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_validation_allocation"
    __table_args__ = (Index("ix_model_validation_allocation_run_stock", "validation_run_id", "stock_code", unique=True),)
    validation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    allocation_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    allocation_purpose: Mapped[str] = mapped_column(String(32), default="MODEL_VALIDATION", nullable=False)
    actionable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="NON_ACTIONABLE", nullable=False)
    relative_allocation_weight: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    suggested_position_percent: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    suggested_capital_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    suggested_quantity: Mapped[int] = mapped_column(nullable=False)
    estimated_max_loss: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    binding_constraints: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
