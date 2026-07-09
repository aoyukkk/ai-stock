from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from datasource.schemas import KlineBar, LimitPriceInfo, RealtimeQuote
from trading.config import VirtualTradingConfig
from trading.rules import is_buy_failed_at_limit_up, is_sell_failed_at_limit_down


@dataclass(frozen=True)
class FillSimulationResult:
    status: str
    filled_quantity: int
    fail_reason: str | None = None


def simulate_order_fill(
    action: str,
    order_price: Decimal,
    order_quantity: int,
    market_snapshot: RealtimeQuote,
    kline_bar: KlineBar,
    limit_price: LimitPriceInfo,
    config: VirtualTradingConfig,
) -> FillSimulationResult:
    action = action.upper()
    if action == "BUY":
        if is_buy_failed_at_limit_up(order_price, limit_price.limit_up_price, config):
            return _partial_or_failed(order_quantity, config, "Buy at limit-up may fail in A-share simulation.")
        if kline_bar.low <= order_price <= limit_price.limit_up_price:
            return FillSimulationResult(status="FILLED", filled_quantity=order_quantity)
        if bool(config.rules.get("support_pending_order", True)):
            return FillSimulationResult(status="PENDING", filled_quantity=0, fail_reason="BUY price was not reached.")
        return FillSimulationResult(status="FAILED", filled_quantity=0, fail_reason="BUY price was not reached.")

    if action == "SELL":
        if is_sell_failed_at_limit_down(order_price, limit_price.limit_down_price, config):
            return _partial_or_failed(order_quantity, config, "Sell at limit-down may fail in A-share simulation.")
        if limit_price.limit_down_price <= order_price <= kline_bar.high:
            return FillSimulationResult(status="FILLED", filled_quantity=order_quantity)
        if bool(config.rules.get("support_pending_order", True)):
            return FillSimulationResult(status="PENDING", filled_quantity=0, fail_reason="SELL price was not reached.")
        return FillSimulationResult(status="FAILED", filled_quantity=0, fail_reason="SELL price was not reached.")

    return FillSimulationResult(status="FAILED", filled_quantity=0, fail_reason=f"Unsupported action: {action}")


def _partial_or_failed(quantity: int, config: VirtualTradingConfig, reason: str) -> FillSimulationResult:
    if bool(config.rules.get("allow_partial_fill", True)):
        ratio = Decimal(str(config.execution.get("default_fill_ratio_when_partial", "0.5")))
        lot_size = int(config.rules.get("lot_size", 100))
        filled = int((Decimal(quantity) * ratio) // Decimal(lot_size)) * lot_size
        if filled > 0:
            return FillSimulationResult(status="PARTIAL_FILLED", filled_quantity=filled, fail_reason=reason)
    return FillSimulationResult(status="FAILED", filled_quantity=0, fail_reason=reason)
