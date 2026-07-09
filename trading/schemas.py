from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


VirtualAction = Literal["BUY", "SELL"]
VirtualOrderStatus = Literal["FILLED", "PARTIAL_FILLED", "FAILED", "PENDING", "CANCELLED", "REPRICED"]


class TradingModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class VirtualAccountSnapshot(TradingModel):
    account_id: int
    name: str
    cash: Decimal
    total_asset: Decimal
    initial_cash: Decimal
    market_value: Decimal
    profit_loss: Decimal
    profit_loss_percent: Decimal
    updated_at: datetime


class VirtualOrderRequest(TradingModel):
    account_id: int
    stock_code: str
    action: VirtualAction
    order_price: Decimal
    order_quantity: int
    order_plan_id: int | None = None
    reason: str | None = None


class VirtualOrderResult(TradingModel):
    order_id: int
    account_id: int
    stock_code: str
    action: VirtualAction
    order_price: Decimal
    order_quantity: int
    filled_quantity: int
    status: VirtualOrderStatus
    fail_reason: str | None = None
    submit_time: datetime
    filled_time: datetime | None = None


class VirtualPositionSnapshot(TradingModel):
    account_id: int
    stock_code: str
    quantity: int
    available_quantity: int
    cost_price: Decimal | None = None
    latest_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    buy_date: date | None = None


class VirtualTradeResult(TradingModel):
    trade_id: int
    account_id: int
    stock_code: str
    action: VirtualAction
    price: Decimal
    quantity: int
    amount: Decimal
    commission: Decimal
    stamp_tax: Decimal
    slippage: Decimal
    order_status: str
    time: datetime
    reason: str | None = None


class VirtualExecutionReport(TradingModel):
    generated_at: datetime
    account: VirtualAccountSnapshot
    orders: list[VirtualOrderResult]
    positions: list[VirtualPositionSnapshot]
    trades: list[VirtualTradeResult]
    summary: dict
