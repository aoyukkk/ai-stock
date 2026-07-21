from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, JSON, Numeric, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


SCORE = Numeric(10, 4)


class AdmissionRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "admission_run"
    __table_args__ = (
        Index("ix_admission_run_trade_status", "trade_date", "status"),
        Index("ix_admission_run_input_hash", "input_hash", unique=True),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    candidate_count: Mapped[int] = mapped_column(nullable=False, default=0)
    pass_count: Mapped[int] = mapped_column(nullable=False, default=0)
    review_count: Mapped[int] = mapped_column(nullable=False, default=0)
    block_count: Mapped[int] = mapped_column(nullable=False, default=0)
    insufficient_count: Mapped[int] = mapped_column(nullable=False, default=0)
    admitted_count: Mapped[int] = mapped_column(nullable=False, default=0)
    manual_challenge_count: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    shadow_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled_in_production: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quant_hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_hash_after: Mapped[str] = mapped_column(String(64), nullable=False)
    llm_call_count: Mapped[int] = mapped_column(nullable=False, default=0)
    external_api_call_count: Mapped[int] = mapped_column(nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EntryTimingResult(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "entry_timing_result"
    __table_args__ = (
        UniqueConstraint("admission_run_id", "stock_code", "pool_type", name="uq_entry_timing_run_stock_pool"),
        Index("ix_entry_timing_trade_status", "trade_date", "admission_status"),
        Index("ix_entry_timing_run_score", "admission_run_id", "entry_timing_score"),
    )

    admission_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(128))
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_rank: Mapped[int | None] = mapped_column()
    quant_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    flash_score: Mapped[Decimal | None] = mapped_column(SCORE)
    position_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    pullback_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    volume_price_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    sector_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    market_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    liquidity_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    entry_timing_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    data_quality_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    admission_status: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    block_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    pool_type: Mapped[str] = mapped_column(String(32), nullable=False, default="AI_POOL")
    manual_score: Mapped[Decimal | None] = mapped_column(SCORE)
    ai_score: Mapped[Decimal | None] = mapped_column(SCORE)
    score_difference: Mapped[Decimal | None] = mapped_column(SCORE)
    diagnostics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)


def _immutable_entry_timing_snapshot(*_args, **_kwargs) -> None:
    raise ValueError("IMMUTABLE_ENTRY_TIMING_SNAPSHOT")


for _model in (AdmissionRun, EntryTimingResult):
    event.listen(_model, "before_update", _immutable_entry_timing_snapshot)
    event.listen(_model, "before_delete", _immutable_entry_timing_snapshot)
