from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


SCORE = Numeric(8, 4)


class StockFactorScore(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "stock_factor_score"
    __table_args__ = (
        Index("ix_stock_factor_score_stock_date", "stock_code", "date"),
        Index("ix_stock_factor_score_date_total", "date", "total_score"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    technical_score: Mapped[Decimal | None] = mapped_column(SCORE)
    capital_score: Mapped[Decimal | None] = mapped_column(SCORE)
    emotion_score: Mapped[Decimal | None] = mapped_column(SCORE)
    momentum_score: Mapped[Decimal | None] = mapped_column(SCORE)
    risk_score: Mapped[Decimal | None] = mapped_column(SCORE)
    total_score: Mapped[Decimal | None] = mapped_column(SCORE)
    factor_version: Mapped[str | None] = mapped_column(String(64))


class StockFactorDetail(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "stock_factor_detail"
    __table_args__ = (
        Index("ix_stock_factor_detail_stock_date", "stock_code", "date"),
        Index("ix_stock_factor_detail_group_name", "factor_group", "factor_name"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    factor_group: Mapped[str] = mapped_column(String(64), nullable=False)
    factor_name: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    normalized_value: Mapped[Decimal | None] = mapped_column(SCORE)
    score: Mapped[Decimal | None] = mapped_column(SCORE)
    weight: Mapped[Decimal | None] = mapped_column(SCORE)
    factor_version: Mapped[str | None] = mapped_column(String(64))
    explain_text: Mapped[str | None] = mapped_column(Text)
