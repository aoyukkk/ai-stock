from datetime import date
from decimal import Decimal

import pytest

from database.models.trading import Position
from trading.config import load_virtual_trading_config
from trading.exceptions import TradingRuleError, VirtualTradingConfigError
from trading.rules import (
    is_buy_failed_at_limit_up,
    is_sell_failed_at_limit_down,
    validate_cash_enough,
    validate_lot_size,
    validate_not_suspended,
    validate_price_limit,
    validate_t_plus_one,
)
from trading.config import VirtualTradingConfig


def test_lot_size_requires_multiple_of_one_hundred() -> None:
    validate_lot_size(100, 100)
    with pytest.raises(TradingRuleError):
        validate_lot_size(150, 100)


def test_cash_insufficient_fails() -> None:
    with pytest.raises(TradingRuleError):
        validate_cash_enough(Decimal("100"), Decimal("101"))


def test_price_limit_rules() -> None:
    with pytest.raises(TradingRuleError):
        validate_price_limit("BUY", Decimal("11.01"), Decimal("11.00"), Decimal("9.00"))
    with pytest.raises(TradingRuleError):
        validate_price_limit("SELL", Decimal("8.99"), Decimal("11.00"), Decimal("9.00"))


def test_t_plus_one_blocks_selling_unavailable_quantity() -> None:
    position = Position(
        account_id=1,
        stock_code="000001",
        quantity=100,
        available_quantity=0,
        buy_date=date(2026, 1, 5),
    )

    with pytest.raises(TradingRuleError):
        validate_t_plus_one("SELL", position, 100, date(2026, 1, 5))


def test_suspended_stock_cannot_trade_when_disabled() -> None:
    with pytest.raises(TradingRuleError):
        validate_not_suspended("SUSPENDED", allow_trade_when_suspended=False)


def test_limit_up_and_limit_down_failure_flags() -> None:
    config = load_virtual_trading_config()

    assert is_buy_failed_at_limit_up(Decimal("11.00"), Decimal("11.00"), config) is True
    assert is_sell_failed_at_limit_down(Decimal("9.00"), Decimal("9.00"), config) is True


def test_real_trading_enabled_config_is_rejected() -> None:
    config = VirtualTradingConfig(raw=load_virtual_trading_config().raw | {"real_trading_enabled": True})

    with pytest.raises(VirtualTradingConfigError):
        config.validate()
