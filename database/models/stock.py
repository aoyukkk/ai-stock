from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, CreatedAtMixin, UpdatedAtMixin


class StockMaster(Base, CreatedAtMixin, UpdatedAtMixin):
    __tablename__ = "stock_master"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    market: Mapped[str | None] = mapped_column(String(20), index=True)
    industry: Mapped[str | None] = mapped_column(String(100), index=True)
    list_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(
        String(20),
        index=True,
        comment="Allowed: NORMAL, ST, SUSPENDED, DELISTED",
    )
