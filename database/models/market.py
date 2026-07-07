from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Index, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, CreatedAtMixin


class StockMarketData(Base, CreatedAtMixin):
    __tablename__ = "stock_market_data"
    __table_args__ = (
        Index("ix_stock_market_data_stock_code_datetime", "stock_code", "datetime"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )
    open: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    close: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    change_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    limit_up_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    limit_down_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))


class PreMarketAuction(Base, CreatedAtMixin):
    __tablename__ = "pre_market_auction"
    __table_args__ = (
        Index("ix_pre_market_auction_stock_code_trade_date", "stock_code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    auction_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    auction_volume: Mapped[int | None] = mapped_column(BigInteger)
    auction_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    auction_change_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    auction_strength_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    raw_json: Mapped[dict | None] = mapped_column(JSON)
