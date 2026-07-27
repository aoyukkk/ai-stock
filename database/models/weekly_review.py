from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class WeeklyRecommendationReviewRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "weekly_recommendation_review_run"
    __table_args__ = (
        Index("ix_weekly_recommendation_review_run_id", "run_id", unique=True),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    review_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    review_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    evaluation_date: Mapped[date] = mapped_column(Date, nullable=False)
    baseline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    sample_status: Mapped[str] = mapped_column(String(48), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    workbook_path: Mapped[str | None] = mapped_column(String(512))
    workbook_hash: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    audit: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class WeeklyRecommendationReviewDetail(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "weekly_recommendation_review_detail"
    __table_args__ = (
        Index(
            "ix_weekly_recommendation_review_detail_unique",
            "run_id",
            "source_type",
            "recommendation_date",
            "stock_code",
            unique=True,
        ),
        Index(
            "ix_weekly_recommendation_review_detail_class",
            "run_id",
            "result_class",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    recommendation_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_trade_date: Mapped[date | None] = mapped_column(Date)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    quant_run_id: Mapped[str | None] = mapped_column(String(64))
    pro_run_id: Mapped[str | None] = mapped_column(String(64))
    quant_rank: Mapped[int | None] = mapped_column()
    quant_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    pro_rank: Mapped[int | None] = mapped_column()
    pro_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    recommendation_grade: Mapped[str | None] = mapped_column(String(32))
    entry_status: Mapped[str] = mapped_column(String(48), nullable=False)
    entry_source: Mapped[str | None] = mapped_column(String(64))
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    max_acceptable_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    stop_loss_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    current_net_return: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    mfe: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    mae: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    giveback: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    stop_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    risk_path_bad: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    result_class: Mapped[str] = mapped_column(String(48), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class WeeklyRecommendationReviewSupersession(IDMixin, ReprMixin, Base):
    __tablename__ = "weekly_recommendation_review_supersession"
    __table_args__ = (
        Index(
            "ix_weekly_recommendation_review_supersession_unique",
            "superseded_run_id",
            "replacement_run_id",
            unique=True,
        ),
    )

    superseded_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    replacement_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
