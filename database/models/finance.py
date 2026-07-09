from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class StockFinance(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "stock_finance"
    __table_args__ = (
        Index("ix_stock_finance_stock_date", "stock_code", "date"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    pe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pb: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    roe: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    debt_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    revenue_growth: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    profit_growth: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
