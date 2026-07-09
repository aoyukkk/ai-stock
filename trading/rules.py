from __future__ import annotations

from datetime import date
from decimal import Decimal

from database.models.trading import Position
from trading.config import VirtualTradingConfig
from trading.exceptions import TradingRuleError


def validate_lot_size(quantity: int, lot_size: int) -> None:
    if quantity <= 0 or quantity % lot_size != 0:
        raise TradingRuleError(f"Order quantity must be a positive multiple of {lot_size}.")


def validate_cash_enough(cash: Decimal, required_amount: Decimal) -> None:
    if cash < required_amount:
        raise TradingRuleError("Cash is insufficient for this virtual order.")


def validate_price_limit(
    action: str,
    order_price: Decimal,
    limit_up_price: Decimal,
    limit_down_price: Decimal,
) -> None:
    if action.upper() == "BUY" and order_price > limit_up_price:
        raise TradingRuleError("BUY price cannot exceed limit-up price.")
    if action.upper() == "SELL" and order_price < limit_down_price:
        raise TradingRuleError("SELL price cannot be below limit-down price.")


def validate_t_plus_one(action: str, position: Position | None, quantity: int, trade_date: date) -> None:
    if action.upper() != "SELL":
        return
    if position is None or position.available_quantity < quantity:
        raise TradingRuleError("SELL quantity cannot exceed available T+1 quantity.")
    if position.buy_date == trade_date and position.available_quantity < quantity:
        raise TradingRuleError("T+1 rule blocks selling today's buy quantity.")


def validate_not_suspended(status: str, allow_trade_when_suspended: bool) -> None:
    if status.upper() == "SUSPENDED" and not allow_trade_when_suspended:
        raise TradingRuleError("Suspended stocks cannot be traded in virtual simulation.")


def is_buy_failed_at_limit_up(order_price: Decimal, limit_up_price: Decimal, config: VirtualTradingConfig) -> bool:
    return bool(config.execution.get("fail_buy_at_limit_up", True)) and order_price >= limit_up_price


def is_sell_failed_at_limit_down(order_price: Decimal, limit_down_price: Decimal, config: VirtualTradingConfig) -> bool:
    return bool(config.execution.get("fail_sell_at_limit_down", True)) and order_price <= limit_down_price
