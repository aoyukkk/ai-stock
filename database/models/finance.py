from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, CreatedAtMixin


class StockFinance(Base, CreatedAtMixin):
    __tablename__ = "stock_finance"
    __table_args__ = (Index("ix_stock_finance_stock_code_date", "stock_code", "date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    date: Mapped[date | None] = mapped_column(Date, index=True)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    pe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pb: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    roe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    debt_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    revenue_growth: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    profit_growth: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
