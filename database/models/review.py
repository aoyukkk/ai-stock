from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


SCORE = Numeric(8, 4)
AMOUNT = Numeric(20, 2)


class DailyReview(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "daily_review"
    __table_args__ = (
        Index("ix_daily_review_date", "date"),
        Index("ix_daily_review_account", "account_id"),
    )

    date: Mapped[date] = mapped_column(Date, nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("trading_account.id"))
    market_summary: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    human_summary: Mapped[str | None] = mapped_column(Text)
    prediction_accuracy: Mapped[Decimal | None] = mapped_column(SCORE)
    order_price_quality: Mapped[Decimal | None] = mapped_column(SCORE)
    profit_loss: Mapped[Decimal | None] = mapped_column(AMOUNT)
    max_drawdown: Mapped[Decimal | None] = mapped_column(SCORE)
    win_rate: Mapped[Decimal | None] = mapped_column(SCORE)
    mistake_analysis: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    final_review_score: Mapped[Decimal | None] = mapped_column(SCORE)
    module_scores: Mapped[dict | None] = mapped_column(JSON)


class PredictionEvaluation(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "prediction_evaluation"
    __table_args__ = (
        Index("ix_prediction_evaluation_prediction", "prediction_record_id"),
        Index("ix_prediction_evaluation_stock_date", "stock_code", "evaluation_date"),
    )

    prediction_record_id: Mapped[int] = mapped_column(ForeignKey("prediction_record.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluation_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_return: Mapped[Decimal | None] = mapped_column(SCORE)
    actual_max_drawdown: Mapped[Decimal | None] = mapped_column(SCORE)
    actual_direction: Mapped[str | None] = mapped_column(String(32))
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    error_reason: Mapped[str | None] = mapped_column(Text)
