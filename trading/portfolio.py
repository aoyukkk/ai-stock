from __future__ import annotations

from decimal import Decimal

from database.models.trading import Position


def calculate_market_value(positions: list[Position], latest_prices: dict[str, Decimal]) -> Decimal:
    total = Decimal("0")
    for position in positions:
        price = latest_prices.get(position.stock_code, position.cost_price or Decimal("0"))
        total += price * Decimal(position.quantity)
    return total.quantize(Decimal("0.0001"))
