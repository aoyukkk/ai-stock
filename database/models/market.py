from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Index, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


PRICE = Numeric(12, 4)
AMOUNT = Numeric(20, 2)
RATIO = Numeric(8, 4)


class StockMarketData(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "stock_market_data"
    __table_args__ = (
        Index("ix_stock_market_data_stock_datetime", "stock_code", "datetime"),
        Index("ix_stock_market_data_datetime", "datetime"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal | None] = mapped_column(PRICE)
    high: Mapped[Decimal | None] = mapped_column(PRICE)
    low: Mapped[Decimal | None] = mapped_column(PRICE)
    close: Mapped[Decimal | None] = mapped_column(PRICE)
    pre_close: Mapped[Decimal | None] = mapped_column(PRICE)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    amount: Mapped[Decimal | None] = mapped_column(AMOUNT)
    turnover_rate: Mapped[Decimal | None] = mapped_column(RATIO)
    change_percent: Mapped[Decimal | None] = mapped_column(RATIO)
    limit_up_price: Mapped[Decimal | None] = mapped_column(PRICE)
    limit_down_price: Mapped[Decimal | None] = mapped_column(PRICE)


class PreMarketAuction(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "pre_market_auction"
    __table_args__ = (
        Index("ix_pre_market_auction_stock_date", "stock_code", "trade_date"),
        Index("ix_pre_market_auction_trade_date", "trade_date"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    auction_price: Mapped[Decimal | None] = mapped_column(PRICE)
    auction_volume: Mapped[int | None] = mapped_column(BigInteger)
    auction_amount: Mapped[Decimal | None] = mapped_column(AMOUNT)
    auction_change_percent: Mapped[Decimal | None] = mapped_column(RATIO)
    auction_strength_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    raw_json: Mapped[dict | None] = mapped_column(JSON)
