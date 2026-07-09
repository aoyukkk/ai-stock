from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.core.config import get_app_config
from alerts.exceptions import AlertConfigError


DEFAULT_EVENT_TRIGGER_CONFIG = {
    "price": {
        "rapid_rise_percent": 8,
        "rapid_drop_percent": -5,
    },
    "volume": {
        "abnormal_ratio": 3,
    },
    "turnover": {
        "abnormal_ratio": 2,
    },
    "news": {
        "importance_threshold": 80,
        "major_negative_threshold": 85,
    },
    "pre_market": {
        "high_open_cancel_threshold_percent": 6,
        "low_open_recheck_threshold_percent": -4,
        "auction_abnormal_enabled": True,
    },
    "order": {
        "near_fill_threshold_percent": 0.3,
        "reprice_threshold_percent": 1.5,
    },
    "emergency_mode": {
        "enabled": True,
        "cooldown_minutes": 5,
        "notify_user": True,
    },
    "recheck": {
        "mode": "advisory_only",
        "allow_virtual_order_update": False,
        "real_order_update_enabled": False,
    },
}


@dataclass(frozen=True)
class AlertRulesConfig:
    raw: dict[str, Any]

    @property
    def price(self) -> dict[str, Decimal]:
        return _decimal_mapping(self.raw, "price")

    @property
    def volume(self) -> dict[str, Decimal]:
        return _decimal_mapping(self.raw, "volume")

    @property
    def turnover(self) -> dict[str, Decimal]:
        return _decimal_mapping(self.raw, "turnover")

    @property
    def news(self) -> dict[str, Decimal]:
        return _decimal_mapping(self.raw, "news")

    @property
    def pre_market(self) -> dict[str, Any]:
        return DEFAULT_EVENT_TRIGGER_CONFIG["pre_market"] | self.raw.get("pre_market", {})

    @property
    def order(self) -> dict[str, Decimal]:
        return _decimal_mapping(self.raw, "order")

    @property
    def emergency_mode(self) -> dict[str, Any]:
        return DEFAULT_EVENT_TRIGGER_CONFIG["emergency_mode"] | self.raw.get("emergency_mode", {})

    @property
    def recheck(self) -> dict[str, Any]:
        return DEFAULT_EVENT_TRIGGER_CONFIG["recheck"] | self.raw.get("recheck", {})

    @property
    def advisory_only(self) -> bool:
        return str(self.recheck.get("mode", "advisory_only")) == "advisory_only"

    def validate(self) -> None:
        if bool(self.recheck.get("real_order_update_enabled", False)):
            raise AlertConfigError("real_order_update_enabled must remain false in Phase 10.")
        if str(self.recheck.get("mode", "advisory_only")) != "advisory_only":
            raise AlertConfigError("Phase 10 requires advisory_only recheck mode by default.")
        if self.order["near_fill_threshold_percent"] < 0:
            raise AlertConfigError("near_fill_threshold_percent cannot be negative.")
        if self.volume["abnormal_ratio"] <= 0 or self.turnover["abnormal_ratio"] <= 0:
            raise AlertConfigError("abnormal ratios must be greater than 0.")

    def summary(self) -> dict[str, Any]:
        return {
            "price": {key: float(value) for key, value in self.price.items()},
            "volume": {key: float(value) for key, value in self.volume.items()},
            "turnover": {key: float(value) for key, value in self.turnover.items()},
            "news": {key: float(value) for key, value in self.news.items()},
            "pre_market": _jsonable(self.pre_market),
            "order": {key: float(value) for key, value in self.order.items()},
            "emergency_mode": _jsonable(self.emergency_mode),
            "recheck": _jsonable(self.recheck),
            "advisory_only": self.advisory_only,
            "real_order_update_enabled": False,
        }


def load_alert_rules_config() -> AlertRulesConfig:
    raw = get_app_config().config_files.get("risk_rules", {}).get("event_trigger", {})
    merged = _deep_merge(DEFAULT_EVENT_TRIGGER_CONFIG, raw)
    return AlertRulesConfig(raw=merged)


def _decimal_mapping(raw: dict[str, Any], key: str) -> dict[str, Decimal]:
    merged = DEFAULT_EVENT_TRIGGER_CONFIG[key] | raw.get(key, {})
    return {
        item_key: Decimal(str(item_value))
        for item_key, item_value in merged.items()
        if not isinstance(item_value, bool) and isinstance(item_value, (int, float, str, Decimal))
    }


def _deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(default)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _jsonable(mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        key: float(value) if isinstance(value, Decimal) else value
        for key, value in mapping.items()
    }
