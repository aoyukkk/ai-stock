from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


SCORE = Numeric(12, 6)
PRICE = Numeric(16, 6)
RATIO = Numeric(18, 10)


class IFindEnhancementRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ifind_enhancement_run"
    __table_args__ = (
        Index("ix_ifind_enhancement_trade_status", "trade_date", "status"),
        UniqueConstraint("input_hash", name="uq_ifind_enhancement_input_hash"),
    )

    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_run_id: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_count: Mapped[int] = mapped_column(nullable=False, default=0)
    coverage_ratio: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    material_conflict_count: Mapped[int] = mapped_column(nullable=False, default=0)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scoring_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    cohort_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IFindStockEnhancementScore(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ifind_stock_enhancement_score"
    __table_args__ = (
        UniqueConstraint("run_id", "stock_code", name="uq_ifind_stock_enhancement_run_stock"),
        Index("ix_ifind_stock_enhancement_rank", "run_id", "enhanced_rank"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    base_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    base_rank: Mapped[int] = mapped_column(nullable=False)
    ifind_eod_score: Mapped[Decimal | None] = mapped_column(SCORE)
    overlay_delta: Mapped[Decimal] = mapped_column(SCORE, nullable=False, default=0)
    enhanced_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    enhanced_rank: Mapped[int] = mapped_column(nullable=False)
    relative_strength_score: Mapped[Decimal | None] = mapped_column(SCORE)
    close_quality_score: Mapped[Decimal | None] = mapped_column(SCORE)
    tail_strength_score: Mapped[Decimal | None] = mapped_column(SCORE)
    intraday_stability_score: Mapped[Decimal | None] = mapped_column(SCORE)
    liquidity_confirmation_score: Mapped[Decimal | None] = mapped_column(SCORE)
    regime_fit_score: Mapped[Decimal | None] = mapped_column(SCORE)
    component_scores_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    data_quality_coefficient: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False)
    minute_completeness: Mapped[Decimal | None] = mapped_column(RATIO)
    dual_source_status: Mapped[str] = mapped_column(String(32), nullable=False)
    scoring_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(64))


class TraderPositionSnapshot(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "trader_position_snapshot"
    __table_args__ = (
        Index("ix_trader_position_current", "account_scope", "is_current"),
        UniqueConstraint("account_scope", "version", "stock_code", name="uq_trader_position_version_stock"),
    )

    account_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name_snapshot: Mapped[str | None] = mapped_column(String(128))
    quantity: Mapped[int] = mapped_column(nullable=False)
    available_quantity: Mapped[int] = mapped_column(nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    buy_date: Mapped[date | None] = mapped_column(Date)
    market_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    position_percent: Mapped[Decimal | None] = mapped_column(RATIO)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TraderPositionImportBatch(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "trader_position_import_batch"

    preview_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    account_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rows_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PositionTruthConfirmation(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "position_truth_confirmation"
    __table_args__ = (
        Index("ix_position_truth_current", "account_scope", "is_current"),
        UniqueConstraint("snapshot_hash", name="uq_position_truth_snapshot_hash"),
    )

    account_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    position_count: Mapped[int] = mapped_column(nullable=False)
    confirmation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_version: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PostCloseActionRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "post_close_action_run"
    __table_args__ = (
        Index("ix_post_close_action_trade_status", "trade_date", "status"),
        UniqueConstraint("input_hash", name="uq_post_close_action_input_hash"),
    )

    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    run_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    pipeline_run_id: Mapped[str | None] = mapped_column(String(128))
    quant_run_id: Mapped[str | None] = mapped_column(String(64))
    enhancement_run_id: Mapped[str | None] = mapped_column(String(64))
    position_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    market_review_run_id: Mapped[str | None] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_count: Mapped[int] = mapped_column(nullable=False, default=0)
    held_count: Mapped[int] = mapped_column(nullable=False, default=0)
    non_held_count: Mapped[int] = mapped_column(nullable=False, default=0)
    rule_duration_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    pro_review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_RUN")
    scoring_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    config_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostCloseActionResult(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "post_close_action_result"
    __table_args__ = (
        UniqueConstraint("run_id", "stock_code", name="uq_post_close_action_result_run_stock"),
        Index("ix_post_close_action_result_action", "run_id", "current_adopted_action"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(128))
    selection_source: Mapped[str] = mapped_column(String(128), nullable=False)
    position_status: Mapped[str] = mapped_column(String(32), nullable=False)
    account_scope: Mapped[str | None] = mapped_column(String(32))
    quantity: Mapped[int | None] = mapped_column()
    available_quantity: Mapped[int | None] = mapped_column()
    target_day_sellable_quantity: Mapped[int | None] = mapped_column()
    cost_price: Mapped[Decimal | None] = mapped_column(PRICE)
    close_price: Mapped[Decimal | None] = mapped_column(PRICE)
    unrealized_return: Mapped[Decimal | None] = mapped_column(RATIO)
    holding_days: Mapped[int | None] = mapped_column()
    base_score: Mapped[Decimal | None] = mapped_column(SCORE)
    ifind_shadow_score: Mapped[Decimal | None] = mapped_column(SCORE)
    enhanced_shadow_score: Mapped[Decimal | None] = mapped_column(SCORE)
    base_rank: Mapped[int | None] = mapped_column()
    enhanced_rank: Mapped[int | None] = mapped_column()
    action_health_score: Mapped[Decimal | None] = mapped_column(SCORE)
    baseline_rule_action: Mapped[str] = mapped_column(String(64), nullable=False)
    ifind_shadow_action: Mapped[str] = mapped_column(String(64), nullable=False)
    pro_review_action: Mapped[str | None] = mapped_column(String(64))
    current_adopted_action: Mapped[str] = mapped_column(String(64), nullable=False)
    current_position_percent: Mapped[Decimal | None] = mapped_column(RATIO)
    suggested_target_position_percent: Mapped[Decimal | None] = mapped_column(RATIO)
    suggested_reduce_percent: Mapped[Decimal | None] = mapped_column(RATIO)
    suggested_reduce_quantity: Mapped[int | None] = mapped_column()
    stop_loss_price: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit_1: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit_2: Mapped[Decimal | None] = mapped_column(PRICE)
    hard_gate_status: Mapped[str] = mapped_column(String(64), nullable=False)
    key_reasons_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    key_risks_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    data_quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    advice_version: Mapped[str] = mapped_column(String(64), nullable=False)
    review_reason: Mapped[str | None] = mapped_column(Text)
