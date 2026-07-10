from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class QuantRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "quant_run"
    __table_args__ = (Index("ix_quant_run_run_id", "run_id", unique=True), Index("ix_quant_run_latest", "temporal_status", "status", "created_at"))
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    run_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_market_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    factor_version: Mapped[str | None] = mapped_column(String(64))
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    data_manifest_id: Mapped[str] = mapped_column(String(64), nullable=False)
    universe_count: Mapped[int] = mapped_column(default=0, nullable=False)
    filtered_count: Mapped[int] = mapped_column(default=0, nullable=False)
    scored_count: Mapped[int] = mapped_column(default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(default=0, nullable=False)
    top_count: Mapped[int] = mapped_column(default=0, nullable=False)
    no_llm_call_verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    trade_date_cache_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    per_stock_api_call_count: Mapped[int] = mapped_column(default=0, nullable=False)
    total_seconds: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    temporal_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actionable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class QuantRankResult(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "quant_rank_result"
    __table_args__ = (Index("ix_quant_rank_result_run_rank", "quant_run_id", "rank", unique=True),)
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    rank: Mapped[int] = mapped_column(nullable=False)
    total_score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    technical_score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    capital_score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    emotion_score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    momentum_score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    risk_score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    factor_detail_reference: Mapped[dict | None] = mapped_column(JSON)
