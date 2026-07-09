from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


PRICE = Numeric(12, 4)
AMOUNT = Numeric(20, 2)


class TradingAccount(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "trading_account"
    __table_args__ = (Index("ix_trading_account_type", "type"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    cash: Mapped[Decimal | None] = mapped_column(AMOUNT)
    total_asset: Mapped[Decimal | None] = mapped_column(AMOUNT)
    initial_cash: Mapped[Decimal | None] = mapped_column(AMOUNT)


class Position(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "position"
    __table_args__ = (
        Index("ix_position_account_stock", "account_id", "stock_code"),
        Index("ix_position_stock_code", "stock_code"),
    )

    account_id: Mapped[int] = mapped_column(ForeignKey("trading_account.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_price: Mapped[Decimal | None] = mapped_column(PRICE)
    available_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    buy_date: Mapped[date | None] = mapped_column(Date)


class TradeOrder(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "trade_order"
    __table_args__ = (
        Index("ix_trade_order_account_submit", "account_id", "submit_time"),
        Index("ix_trade_order_stock_submit", "stock_code", "submit_time"),
        Index("ix_trade_order_order_plan", "order_plan_id"),
    )

    account_id: Mapped[int] = mapped_column(ForeignKey("trading_account.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    order_price: Mapped[Decimal | None] = mapped_column(PRICE)
    order_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    submit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fail_reason: Mapped[str | None] = mapped_column(Text)
    order_plan_id: Mapped[int | None] = mapped_column(ForeignKey("order_plan.id"))


class TradeRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "trade_record"
    __table_args__ = (
        Index("ix_trade_record_account_time", "account_id", "time"),
        Index("ix_trade_record_stock_time", "stock_code", "time"),
        Index("ix_trade_record_prediction", "prediction_record_id"),
        Index("ix_trade_record_order_plan", "order_plan_id"),
    )

    account_id: Mapped[int] = mapped_column(ForeignKey("trading_account.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(PRICE)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(AMOUNT)
    commission: Mapped[Decimal | None] = mapped_column(AMOUNT)
    stamp_tax: Mapped[Decimal | None] = mapped_column(AMOUNT)
    slippage: Mapped[Decimal | None] = mapped_column(AMOUNT)
    order_status: Mapped[str | None] = mapped_column(String(32))
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    prediction_record_id: Mapped[int | None] = mapped_column(ForeignKey("prediction_record.id"))
    order_plan_id: Mapped[int | None] = mapped_column(ForeignKey("order_plan.id"))
