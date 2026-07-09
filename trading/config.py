from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.core.config import get_app_config
from trading.exceptions import VirtualTradingConfigError


DEFAULT_VIRTUAL_TRADING_CONFIG = {
    "enabled": True,
    "real_trading_enabled": False,
    "account_type": "AI_SIMULATION",
    "initial_cash": 1000000,
    "rules": {
        "t_plus_one": True,
        "price_limit": True,
        "lot_size": 100,
        "allow_partial_fill": True,
        "allow_trade_when_suspended": False,
        "support_pending_order": True,
        "support_cancel_order": True,
        "support_reprice_order": True,
    },
    "cost": {
        "commission_rate": 0.0003,
        "min_commission": 5,
        "stamp_tax_rate": 0.001,
        "stamp_tax_side": "sell",
        "slippage_rate": 0.0005,
    },
    "execution": {
        "fail_buy_at_limit_up": True,
        "fail_sell_at_limit_down": True,
        "default_fill_ratio_when_partial": 0.5,
    },
}


@dataclass(frozen=True)
class VirtualTradingConfig:
    raw: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", True))

    @property
    def real_trading_enabled(self) -> bool:
        return bool(self.raw.get("real_trading_enabled", False))

    @property
    def account_type(self) -> str:
        return str(self.raw.get("account_type", "AI_SIMULATION"))

    @property
    def initial_cash(self) -> Decimal:
        return Decimal(str(self.raw.get("initial_cash", "1000000")))

    @property
    def rules(self) -> dict[str, Any]:
        return DEFAULT_VIRTUAL_TRADING_CONFIG["rules"] | self.raw.get("rules", {})

    @property
    def cost(self) -> dict[str, Decimal | str]:
        merged = DEFAULT_VIRTUAL_TRADING_CONFIG["cost"] | self.raw.get("cost", {})
        return {
            key: str(value).lower() if key == "stamp_tax_side" else Decimal(str(value))
            for key, value in merged.items()
        }

    @property
    def execution(self) -> dict[str, Any]:
        return DEFAULT_VIRTUAL_TRADING_CONFIG["execution"] | self.raw.get("execution", {})

    def validate(self) -> None:
        if self.real_trading_enabled:
            raise VirtualTradingConfigError("Real trading must remain disabled in Phase 9.")
        if self.initial_cash <= 0:
            raise VirtualTradingConfigError("initial_cash must be greater than 0.")
        if int(self.rules.get("lot_size", 100)) <= 0:
            raise VirtualTradingConfigError("lot_size must be greater than 0.")
        for key in ("commission_rate", "min_commission", "stamp_tax_rate", "slippage_rate"):
            if Decimal(str(self.cost[key])) < 0:
                raise VirtualTradingConfigError(f"{key} cannot be negative.")

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "real_trading_enabled": self.real_trading_enabled,
            "account_type": self.account_type,
            "initial_cash": float(self.initial_cash),
            "rules": self.rules,
            "cost": {
                key: (float(value) if isinstance(value, Decimal) else value)
                for key, value in self.cost.items()
            },
            "execution": self.execution,
            "broker": {
                "real_broker_enabled": False,
                "virtual_only": True,
            },
        }


def load_virtual_trading_config() -> VirtualTradingConfig:
    raw = get_app_config().config_files.get("virtual_trading", {}).get("virtual_trading", {})
    merged = _deep_merge(DEFAULT_VIRTUAL_TRADING_CONFIG, raw)
    return VirtualTradingConfig(raw=merged)


def _deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(default)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
