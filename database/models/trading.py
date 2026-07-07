from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, CreatedAtMixin, UpdatedAtMixin


class TradingAccount(Base, CreatedAtMixin, UpdatedAtMixin):
    __tablename__ = "trading_account"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(100))
    type: Mapped[str | None] = mapped_column(
        String(30),
        index=True,
        comment="Allowed: human, ai_simulation",
    )
    cash: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_asset: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    initial_cash: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))


class Position(Base, UpdatedAtMixin):
    __tablename__ = "position"
    __table_args__ = (Index("ix_position_account_id_stock_code", "account_id", "stock_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    quantity: Mapped[int | None] = mapped_column()
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    available_quantity: Mapped[int | None] = mapped_column()
    buy_date: Mapped[date | None] = mapped_column(Date)


class TradeRecord(Base, CreatedAtMixin):
    __tablename__ = "trade_record"
    __table_args__ = (
        Index("ix_trade_record_account_id_time", "account_id", "time"),
        Index("ix_trade_record_stock_code_time", "stock_code", "time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    action: Mapped[str | None] = mapped_column(String(10), comment="Allowed: BUY, SELL")
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    quantity: Mapped[int | None] = mapped_column()
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    commission: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    stamp_tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    slippage: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    order_status: Mapped[str | None] = mapped_column(
        String(30),
        comment="Allowed: FILLED, PARTIAL_FILLED, FAILED, CANCELLED",
    )
    time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    prediction_record_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    order_plan_id: Mapped[int | None] = mapped_column(BigInteger, index=True)


class TradeOrder(Base, CreatedAtMixin):
    __tablename__ = "trade_order"
    __table_args__ = (
        Index("ix_trade_order_account_id_submit_time", "account_id", "submit_time"),
        Index("ix_trade_order_stock_code_submit_time", "stock_code", "submit_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    action: Mapped[str | None] = mapped_column(String(10), comment="Allowed: BUY, SELL")
    order_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    order_quantity: Mapped[int | None] = mapped_column()
    filled_quantity: Mapped[int | None] = mapped_column()
    status: Mapped[str | None] = mapped_column(String(30), index=True)
    submit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    filled_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fail_reason: Mapped[str | None] = mapped_column(Text)
    order_plan_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
