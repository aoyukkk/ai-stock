from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, CreatedAtMixin


class StockFactorScore(Base, CreatedAtMixin):
    __tablename__ = "stock_factor_score"
    __table_args__ = (
        Index("ix_stock_factor_score_stock_code_date", "stock_code", "date"),
        Index("ix_stock_factor_score_date_total_score", "date", "total_score"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    date: Mapped[date | None] = mapped_column(Date, index=True)
    technical_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    capital_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    emotion_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    momentum_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), index=True)
    factor_version: Mapped[str | None] = mapped_column(String(50))


class StockFactorDetail(Base, CreatedAtMixin):
    __tablename__ = "stock_factor_detail"
    __table_args__ = (
        Index("ix_stock_factor_detail_stock_code_date", "stock_code", "date"),
        Index("ix_stock_factor_detail_group_name", "factor_group", "factor_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    date: Mapped[date | None] = mapped_column(Date, index=True)
    factor_group: Mapped[str | None] = mapped_column(String(50), index=True)
    factor_name: Mapped[str | None] = mapped_column(String(100), index=True)
    raw_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    normalized_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    weight: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    factor_version: Mapped[str | None] = mapped_column(String(50))
    explain_text: Mapped[str | None] = mapped_column(Text)
