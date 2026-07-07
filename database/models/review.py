from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, CreatedAtMixin


class DailyReview(Base, CreatedAtMixin):
    __tablename__ = "daily_review"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    date: Mapped[date | None] = mapped_column(Date, index=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    market_summary: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    human_summary: Mapped[str | None] = mapped_column(Text)
    prediction_accuracy: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    order_price_quality: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    profit_loss: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    mistake_analysis: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)


class PredictionEvaluation(Base, CreatedAtMixin):
    __tablename__ = "prediction_evaluation"
    __table_args__ = (
        Index("ix_prediction_evaluation_stock_code_evaluation_date", "stock_code", "evaluation_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    prediction_record_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    evaluation_date: Mapped[date | None] = mapped_column(Date, index=True)
    actual_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    actual_max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    actual_direction: Mapped[str | None] = mapped_column(String(20))
    is_correct: Mapped[bool | None] = mapped_column()
    error_reason: Mapped[str | None] = mapped_column(Text)
