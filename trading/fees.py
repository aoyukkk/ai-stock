from __future__ import annotations

from decimal import Decimal

from trading.config import VirtualTradingConfig


MONEY_QUANT = Decimal("0.0001")


def calculate_commission(amount: Decimal, commission_rate: Decimal, min_commission: Decimal) -> Decimal:
    if amount <= 0:
        return Decimal("0.0000")
    return max(amount * commission_rate, min_commission).quantize(MONEY_QUANT)


def calculate_stamp_tax(
    amount: Decimal,
    action: str,
    stamp_tax_rate: Decimal,
    stamp_tax_side: str = "sell",
) -> Decimal:
    if amount <= 0:
        return Decimal("0.0000")
    should_charge = action.upper() == stamp_tax_side.upper()
    return (amount * stamp_tax_rate).quantize(MONEY_QUANT) if should_charge else Decimal("0.0000")


def calculate_slippage(price: Decimal, action: str, slippage_rate: Decimal) -> Decimal:
    if action.upper() == "BUY":
        return (price * (Decimal("1") + slippage_rate)).quantize(MONEY_QUANT)
    if action.upper() == "SELL":
        return (price * (Decimal("1") - slippage_rate)).quantize(MONEY_QUANT)
    return price.quantize(MONEY_QUANT)


def calculate_total_cost(action: str, price: Decimal, quantity: int, config: VirtualTradingConfig) -> dict[str, Decimal]:
    cost = config.cost
    actual_price = calculate_slippage(price, action, cost["slippage_rate"])  # type: ignore[arg-type]
    amount = (actual_price * Decimal(quantity)).quantize(MONEY_QUANT)
    slippage = (abs(actual_price - price) * Decimal(quantity)).quantize(MONEY_QUANT)
    commission = calculate_commission(
        amount,
        cost["commission_rate"],  # type: ignore[arg-type]
        cost["min_commission"],  # type: ignore[arg-type]
    )
    stamp_tax = calculate_stamp_tax(
        amount,
        action,
        cost["stamp_tax_rate"],  # type: ignore[arg-type]
        str(cost["stamp_tax_side"]),
    )
    cash_delta = amount + commission + stamp_tax if action.upper() == "BUY" else amount - commission - stamp_tax
    return {
        "price": actual_price,
        "amount": amount,
        "commission": commission,
        "stamp_tax": stamp_tax,
        "slippage": slippage,
        "cash_delta": cash_delta.quantize(MONEY_QUANT),
    }
