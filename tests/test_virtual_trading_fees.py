from decimal import Decimal

from trading.config import load_virtual_trading_config
from trading.fees import calculate_commission, calculate_slippage, calculate_stamp_tax, calculate_total_cost


def test_buy_has_no_stamp_tax() -> None:
    assert calculate_stamp_tax(Decimal("10000"), "BUY", Decimal("0.001"), "sell") == Decimal("0.0000")


def test_sell_has_stamp_tax() -> None:
    assert calculate_stamp_tax(Decimal("10000"), "SELL", Decimal("0.001"), "sell") == Decimal("10.0000")


def test_minimum_commission_applies() -> None:
    assert calculate_commission(Decimal("1000"), Decimal("0.0003"), Decimal("5")) == Decimal("5.0000")


def test_buy_slippage_increases_price_and_sell_slippage_decreases_price() -> None:
    assert calculate_slippage(Decimal("10.00"), "BUY", Decimal("0.0005")) > Decimal("10.00")
    assert calculate_slippage(Decimal("10.00"), "SELL", Decimal("0.0005")) < Decimal("10.00")


def test_total_cost_uses_decimal_fee_components() -> None:
    result = calculate_total_cost("BUY", Decimal("10.00"), 100, load_virtual_trading_config())

    assert result["amount"] > 0
    assert result["commission"] >= Decimal("5")
    assert result["stamp_tax"] == Decimal("0.0000")
    assert result["cash_delta"] == result["amount"] + result["commission"]
