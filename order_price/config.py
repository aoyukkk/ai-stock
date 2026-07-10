from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.core.config import get_app_config
from order_price.exceptions import OrderPriceConfigError


DEFAULT_ORDER_PRICE_CONFIG = {
    "enabled": True,
    "atr_window": 14,
    "tick_size": 0.01,
    "max_chase_percent": 0.05,
    "min_risk_reward": 1.5,
    "ideal_risk_reward": 2.0,
    "max_stop_loss_percent": 0.08,
    "stop_validation_tolerance_ticks": 1,
    "default_position_percent": 0.10,
    "risk_reward_target": {
        "mode": "TAKE_PROFIT_2",
        "allowed": ["TAKE_PROFIT_1", "TAKE_PROFIT_2", "EXPECTED_PROFIT_PRICE"],
    },
    "price_levels": {
        "conservative": True,
        "balanced": True,
        "aggressive": True,
    },
    "conservative": {
        "support_atr_buffer": 0.05,
    },
    "balanced": {
        "support_weight": 0.50,
        "vwap_weight": 0.30,
        "previous_close_weight": 0.20,
        "positive_adjustment_atr": 0.10,
        "negative_adjustment_atr": -0.10,
    },
    "aggressive": {
        "breakout_tick_buffer": 0.01,
    },
    "max_buy_price": {
        "use_limit_up": True,
        "atr_multiplier": 0.80,
        "max_chase_percent": 0.05,
    },
    "stop_loss": {
        "atr_multiplier": 1.00,
        "max_stop_loss_percent": 0.08,
    },
    "take_profit": {
        "first_atr_multiplier": 1.00,
        "second_atr_multiplier": 2.00,
    },
    "order_score_weights": {
        "expected_return": 0.35,
        "fill_probability": 0.25,
        "risk_reward": 0.20,
        "emotion": 0.10,
        "capital_confirmation": 0.10,
    },
    "cancel_conditions": {
        "high_open_percent": 6,
        "major_negative_news": True,
        "risk_level_block": ["HIGH", "BLACK_SWAN"],
        "sector_heat_drop": True,
    },
    "reprice_conditions": {
        "price_deviation_percent": 1.5,
        "auction_changed": True,
        "volatility_changed": True,
    },
}


@dataclass(frozen=True)
class OrderPriceConfig:
    raw: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", True))

    @property
    def atr_window(self) -> int:
        return int(self.raw.get("atr_window", 14))

    @property
    def tick_size(self) -> Decimal:
        return Decimal(str(self.raw.get("tick_size", "0.01")))

    @property
    def max_chase_percent(self) -> Decimal:
        return Decimal(str(self.raw.get("max_chase_percent", "0.05")))

    @property
    def min_risk_reward(self) -> Decimal:
        return Decimal(str(self.raw.get("min_risk_reward", "1.5")))

    @property
    def ideal_risk_reward(self) -> Decimal:
        return Decimal(str(self.raw.get("ideal_risk_reward", "2.0")))

    @property
    def max_stop_loss_percent(self) -> Decimal:
        return Decimal(str(self.raw.get("max_stop_loss_percent", "0.08")))

    @property
    def default_position_percent(self) -> Decimal:
        return Decimal(str(self.raw.get("default_position_percent", "0.10")))

    @property
    def stop_validation_tolerance_ticks(self) -> int:
        return int(self.raw.get("stop_validation_tolerance_ticks", 1))

    @property
    def risk_reward_target_mode(self) -> str:
        return str((self.raw.get("risk_reward_target") or {}).get("mode") or "TAKE_PROFIT_2")

    @property
    def risk_reward_target_allowed(self) -> list[str]:
        return list((self.raw.get("risk_reward_target") or {}).get("allowed") or [])

    @property
    def price_levels(self) -> dict[str, bool]:
        raw = self.raw.get("price_levels", {})
        defaults = DEFAULT_ORDER_PRICE_CONFIG["price_levels"]
        return {key: bool(raw.get(key, defaults[key])) for key in defaults}

    @property
    def conservative(self) -> dict[str, Decimal]:
        return _decimal_mapping("conservative", self.raw)

    @property
    def balanced(self) -> dict[str, Decimal]:
        return _decimal_mapping("balanced", self.raw)

    @property
    def aggressive(self) -> dict[str, Decimal]:
        return _decimal_mapping("aggressive", self.raw)

    @property
    def max_buy_price(self) -> dict[str, Any]:
        return _mixed_mapping("max_buy_price", self.raw)

    @property
    def stop_loss(self) -> dict[str, Decimal]:
        return _decimal_mapping("stop_loss", self.raw)

    @property
    def take_profit(self) -> dict[str, Decimal]:
        return _decimal_mapping("take_profit", self.raw)

    @property
    def order_score_weights(self) -> dict[str, Decimal]:
        return _decimal_mapping("order_score_weights", self.raw)

    @property
    def cancel_conditions(self) -> dict[str, Any]:
        return DEFAULT_ORDER_PRICE_CONFIG["cancel_conditions"] | self.raw.get("cancel_conditions", {})

    @property
    def reprice_conditions(self) -> dict[str, Any]:
        return DEFAULT_ORDER_PRICE_CONFIG["reprice_conditions"] | self.raw.get("reprice_conditions", {})

    def validate(self) -> None:
        if self.atr_window <= 0:
            raise OrderPriceConfigError("atr_window must be greater than 0.")
        if self.tick_size <= 0:
            raise OrderPriceConfigError("tick_size must be greater than 0.")
        if not (Decimal("0") <= self.max_chase_percent <= Decimal("1")):
            raise OrderPriceConfigError("max_chase_percent must be between 0 and 1.")
        if self.min_risk_reward <= 0:
            raise OrderPriceConfigError("min_risk_reward must be greater than 0.")
        if self.stop_validation_tolerance_ticks < 0:
            raise OrderPriceConfigError("stop_validation_tolerance_ticks must be non-negative.")
        if self.risk_reward_target_mode not in self.risk_reward_target_allowed:
            raise OrderPriceConfigError("risk_reward_target.mode must be in risk_reward_target.allowed.")
        total = sum(self.order_score_weights.values(), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("0.0001"):
            raise OrderPriceConfigError(f"order_score_weights must sum to 1.0, got {total}.")

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "atr_window": self.atr_window,
            "tick_size": float(self.tick_size),
            "max_chase_percent": float(self.max_chase_percent),
            "min_risk_reward": float(self.min_risk_reward),
            "ideal_risk_reward": float(self.ideal_risk_reward),
            "max_stop_loss_percent": float(self.max_stop_loss_percent),
            "default_position_percent": float(self.default_position_percent),
            "stop_validation_tolerance_ticks": self.stop_validation_tolerance_ticks,
            "risk_reward_target": {
                "mode": self.risk_reward_target_mode,
                "allowed": self.risk_reward_target_allowed,
            },
            "price_levels": self.price_levels,
            "order_score_weights": {key: float(value) for key, value in self.order_score_weights.items()},
            "cancel_conditions": self.cancel_conditions,
            "reprice_conditions": self.reprice_conditions,
            "llm_can_generate_price": False,
            "requires_rule_engine": True,
            "requires_market_data": True,
        }


def load_order_price_config(manager=None) -> OrderPriceConfig:
    raw = get_app_config().config_files.get("order_price", {}).get("order_price", {})
    merged = _deep_merge(DEFAULT_ORDER_PRICE_CONFIG, raw)
    from backend.core.config_manager import ConfigManager

    values = (manager or ConfigManager()).get_effective_config()["values"]
    merged.update({
        "atr_window": values["order_price.atr_window"],
        "tick_size": values["order_price.tick_size"],
        "max_chase_percent": values["order_price.max_chase_percent"],
        "min_risk_reward": values["order_price.min_risk_reward"],
        "ideal_risk_reward": values["order_price.ideal_risk_reward"],
        "max_stop_loss_percent": values["order_price.max_stop_loss_percent"],
        "stop_validation_tolerance_ticks": values["order_price.stop_validation_tolerance_ticks"],
        "default_position_percent": values["order_price.default_position_percent"],
    })
    merged["risk_reward_target"] = {
        **dict(merged.get("risk_reward_target") or {}),
        "mode": values["order_price.risk_reward_target_mode"],
    }
    return OrderPriceConfig(raw=merged)


def _decimal_mapping(key: str, raw: dict[str, Any]) -> dict[str, Decimal]:
    default = DEFAULT_ORDER_PRICE_CONFIG[key]
    value = default | raw.get(key, {})
    return {item_key: Decimal(str(item_value)) for item_key, item_value in value.items()}


def _mixed_mapping(key: str, raw: dict[str, Any]) -> dict[str, Any]:
    default = DEFAULT_ORDER_PRICE_CONFIG[key]
    value = default | raw.get(key, {})
    return {
        item_key: Decimal(str(item_value)) if isinstance(item_value, (int, float, str)) and item_key != "use_limit_up" else item_value
        for item_key, item_value in value.items()
    }


def _deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(default)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
