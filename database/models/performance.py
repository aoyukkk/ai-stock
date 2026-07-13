from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


RATIO = Numeric(18, 10)
PRICE = Numeric(16, 6)


class SelectionPerformanceRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "selection_performance_run"
    __table_args__ = (
        Index("ix_selection_performance_hash_status", "performance_input_hash", "status"),
        Index("ix_selection_performance_end_date", "evaluation_end_date"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    performance_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    lookback_value: Mapped[int] = mapped_column(nullable=False)
    lookback_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    start_selection_date: Mapped[date | None] = mapped_column(Date)
    end_selection_date: Mapped[date | None] = mapped_column(Date)
    return_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    weighting_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    include_zero_position_stocks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_risk_blocked_stocks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cohort_count: Mapped[int] = mapped_column(nullable=False, default=0)
    stock_count: Mapped[int] = mapped_column(nullable=False, default=0)
    daily_record_count: Mapped[int] = mapped_column(nullable=False, default=0)
    portfolio_record_count: Mapped[int] = mapped_column(nullable=False, default=0)
    coverage_ratio: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    market_data_watermark_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_calendar_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cache_checksum: Mapped[str | None] = mapped_column(String(64))
    cache_row_count: Mapped[int | None] = mapped_column()
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SelectionCohort(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "selection_cohort"
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", name="uq_selection_cohort_pipeline_run"),
        Index("ix_selection_cohort_date", "selection_trade_date"),
    )

    selection_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    pipeline_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_run_id: Mapped[str | None] = mapped_column(String(64))
    flash_run_id: Mapped[str | None] = mapped_column(String(64))
    pro_run_id: Mapped[str | None] = mapped_column(String(64))
    position_run_id: Mapped[str | None] = mapped_column(String(64))
    candidate_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_count: Mapped[int] = mapped_column(nullable=False)
    llm_count: Mapped[int] = mapped_column(nullable=False, default=0)
    manual_count: Mapped[int] = mapped_column(nullable=False, default=0)
    both_count: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SelectionCohortMember(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "selection_cohort_member"
    __table_args__ = (
        UniqueConstraint("cohort_id", "stock_code", name="uq_selection_cohort_member_stock"),
        Index("ix_selection_cohort_member_code", "stock_code"),
    )

    cohort_id: Mapped[int] = mapped_column(ForeignKey("selection_cohort.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name_snapshot: Mapped[str] = mapped_column(String(128), nullable=False)
    selection_source: Mapped[str] = mapped_column(String(16), nullable=False)
    quant_rank: Mapped[int | None] = mapped_column()
    quant_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    flash_rank: Mapped[int | None] = mapped_column()
    flash_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    flash_decision: Mapped[str | None] = mapped_column(String(64))
    pro_rank: Mapped[int | None] = mapped_column()
    pro_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    suggested_position_percent: Mapped[Decimal | None] = mapped_column(RATIO)
    risk_status: Mapped[str | None] = mapped_column(String(64))
    baseline_trade_date: Mapped[date | None] = mapped_column(Date)
    baseline_price: Mapped[Decimal | None] = mapped_column(PRICE)


class SelectionPerformanceDaily(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "selection_performance_daily"
    __table_args__ = (
        UniqueConstraint("performance_run_id", "cohort_member_id", "evaluation_trade_date", name="uq_selection_performance_member_date"),
        Index("ix_selection_performance_daily_run_date", "performance_run_id", "evaluation_trade_date"),
    )

    performance_run_id: Mapped[int] = mapped_column(ForeignKey("selection_performance_run.id"), nullable=False)
    cohort_member_id: Mapped[int] = mapped_column(ForeignKey("selection_cohort_member.id"), nullable=False)
    evaluation_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    holding_day: Mapped[int] = mapped_column(nullable=False)
    baseline_trade_date: Mapped[date | None] = mapped_column(Date)
    baseline_price: Mapped[Decimal | None] = mapped_column(PRICE)
    open_price: Mapped[Decimal | None] = mapped_column(PRICE)
    high_price: Mapped[Decimal | None] = mapped_column(PRICE)
    low_price: Mapped[Decimal | None] = mapped_column(PRICE)
    close_price: Mapped[Decimal | None] = mapped_column(PRICE)
    previous_close: Mapped[Decimal | None] = mapped_column(PRICE)
    daily_return: Mapped[Decimal | None] = mapped_column(RATIO)
    cumulative_return: Mapped[Decimal | None] = mapped_column(RATIO)
    peak_cumulative_return: Mapped[Decimal | None] = mapped_column(RATIO)
    drawdown_to_date: Mapped[Decimal | None] = mapped_column(RATIO)
    max_drawdown_to_date: Mapped[Decimal | None] = mapped_column(RATIO)
    return_source: Mapped[str] = mapped_column(String(32), nullable=False)
    data_status: Mapped[str] = mapped_column(String(64), nullable=False)


class SelectionPortfolioDaily(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "selection_portfolio_daily"
    __table_args__ = (
        UniqueConstraint("performance_run_id", "cohort_id", "evaluation_trade_date", "weighting_mode", name="uq_selection_portfolio_run_date_mode"),
        Index("ix_selection_portfolio_daily_run_date", "performance_run_id", "evaluation_trade_date"),
    )

    performance_run_id: Mapped[int] = mapped_column(ForeignKey("selection_performance_run.id"), nullable=False)
    cohort_id: Mapped[int] = mapped_column(ForeignKey("selection_cohort.id"), nullable=False)
    evaluation_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    holding_day: Mapped[int] = mapped_column(nullable=False)
    weighting_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    total_member_count: Mapped[int] = mapped_column(nullable=False)
    valid_member_count: Mapped[int] = mapped_column(nullable=False)
    suspended_count: Mapped[int] = mapped_column(nullable=False)
    missing_count: Mapped[int] = mapped_column(nullable=False)
    daily_return: Mapped[Decimal | None] = mapped_column(RATIO)
    cumulative_return: Mapped[Decimal | None] = mapped_column(RATIO)
    win_rate: Mapped[Decimal | None] = mapped_column(RATIO)
    drawdown_to_date: Mapped[Decimal | None] = mapped_column(RATIO)
    max_drawdown_to_date: Mapped[Decimal | None] = mapped_column(RATIO)
    coverage_ratio: Mapped[Decimal] = mapped_column(RATIO, nullable=False)
    best_stock_code: Mapped[str | None] = mapped_column(String(32))
    worst_stock_code: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(64), nullable=False)


def _immutable_snapshot(*_args, **_kwargs) -> None:
    raise ValueError("IMMUTABLE_SELECTION_SNAPSHOT")


event.listen(SelectionCohort, "before_update", _immutable_snapshot)
event.listen(SelectionCohortMember, "before_update", _immutable_snapshot)
