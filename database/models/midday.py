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


class MiddayRecommendationRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "midday_recommendation_run"
    __table_args__ = (
        Index("ix_midday_run_trade_status", "session_trade_date", "status"),
        UniqueConstraint("input_hash", name="uq_midday_run_input_hash"),
    )

    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    session_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    decision_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_session: Mapped[str] = mapped_column(String(32), nullable=False)
    baseline_trade_date: Mapped[date | None] = mapped_column(Date)
    baseline_quant_run_id: Mapped[str | None] = mapped_column(String(64))
    baseline_manifest_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    current_stage: Mapped[str] = mapped_column(String(40), nullable=False)
    target_session: Mapped[str] = mapped_column(String(32), nullable=False, default="AFTERNOON_SESSION")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recheck_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_mode: Mapped[str] = mapped_column(String(80), nullable=False)
    base_pool_count: Mapped[int] = mapped_column(nullable=False, default=0)
    snapshot_count: Mapped[int] = mapped_column(nullable=False, default=0)
    minute_count: Mapped[int] = mapped_column(nullable=False, default=0)
    flash_count: Mapped[int] = mapped_column(nullable=False, default=0)
    pro_count: Mapped[int] = mapped_column(nullable=False, default=0)
    final_count: Mapped[int] = mapped_column(nullable=False, default=0)
    held_count: Mapped[int] = mapped_column(nullable=False, default=0)
    token_usage: Mapped[int] = mapped_column(nullable=False, default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    total_duration_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checkpoint_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    excel_path: Mapped[str | None] = mapped_column(String(512))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MiddayRecommendationResult(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "midday_recommendation_result"
    __table_args__ = (
        UniqueConstraint("run_id", "stock_code", name="uq_midday_result_run_stock"),
        Index("ix_midday_result_rank", "run_id", "pro_rank"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(128))
    pool_sources: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    position_status: Mapped[str] = mapped_column(String(32), nullable=False)
    base_quant_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    base_quant_rank: Mapped[int] = mapped_column(nullable=False)
    feature_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    midday_overlay_score: Mapped[Decimal | None] = mapped_column(SCORE)
    midday_delta: Mapped[Decimal] = mapped_column(SCORE, nullable=False, default=0)
    midday_enhanced_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    quick_snapshot_rank: Mapped[int | None] = mapped_column()
    minute_rank: Mapped[int | None] = mapped_column()
    midday_flash_score: Mapped[Decimal | None] = mapped_column(SCORE)
    flash_decision: Mapped[str | None] = mapped_column(String(24))
    midday_pro_score: Mapped[Decimal | None] = mapped_column(SCORE)
    pro_rank: Mapped[int | None] = mapped_column()
    hard_gate_status: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_action: Mapped[str] = mapped_column(String(40), nullable=False)
    held_action: Mapped[str | None] = mapped_column(String(40))
    recommended_price: Mapped[Decimal | None] = mapped_column(PRICE)
    max_acceptable_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_loss: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit_1: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit_2: Mapped[Decimal | None] = mapped_column(PRICE)
    suggested_weight: Mapped[Decimal | None] = mapped_column(RATIO)
    suggested_position_percent: Mapped[Decimal | None] = mapped_column(RATIO)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recheck_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    key_reasons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    key_risks: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    data_quality: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    actionable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MiddayRecheckResult(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "midday_recheck_result"
    __table_args__ = (UniqueConstraint("midday_run_id", "stock_code", "recheck_time", name="uq_midday_recheck_stock_time"),)

    midday_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    recheck_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latest_price: Mapped[Decimal | None] = mapped_column(PRICE)
    index_status: Mapped[str] = mapped_column(String(32), nullable=False)
    freshness: Mapped[str] = mapped_column(String(32), nullable=False)
    price_deviation: Mapped[Decimal | None] = mapped_column(RATIO)
    hard_gate_status: Mapped[str] = mapped_column(String(32), nullable=False)
    recheck_status: Mapped[str] = mapped_column(String(32), nullable=False)
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
