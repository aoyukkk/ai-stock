from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class StockMaster(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "stock_master"
    __table_args__ = (
        Index("ix_stock_master_code", "code"),
        Index("ix_stock_master_industry", "industry"),
        Index("ix_stock_master_status", "status"),
    )

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str | None] = mapped_column(String(32))
    industry: Mapped[str | None] = mapped_column(String(128))
    list_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(32))
