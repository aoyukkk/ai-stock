from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import yaml

from backend.core.config import CONFIG_DIR, get_app_config
from review.exceptions import ReviewConfigError


DEFAULT_REVIEW_CONFIG: dict[str, Any] = {
    "review": {
        "enabled": True,
        "use_mock_llm_summary": True,
        "prediction_horizon_days": 1,
        "default_account_type": "ai_simulation",
    },
    "scoring": {
        "prediction_accuracy_weight": 0.30,
        "order_price_quality_weight": 0.30,
        "portfolio_performance_weight": 0.25,
        "risk_control_weight": 0.15,
    },
    "order_price_quality": {
        "filled_score": 80,
        "missed_but_good_price_score": 60,
        "too_aggressive_penalty": 20,
        "too_conservative_penalty": 15,
        "risk_avoided_bonus": 10,
    },
    "thresholds": {
        "good_review_score": 80,
        "neutral_review_score": 60,
        "max_acceptable_drawdown_percent": 8,
    },
}


@dataclass(frozen=True)
class ReviewConfig:
    raw: dict[str, Any]

    @property
    def review(self) -> dict[str, Any]:
        return self.raw.get("review", {})

    @property
    def scoring(self) -> dict[str, Decimal]:
        return _decimal_mapping(self.raw.get("scoring", {}))

    @property
    def order_price_quality(self) -> dict[str, Decimal]:
        return _decimal_mapping(self.raw.get("order_price_quality", {}))

    @property
    def thresholds(self) -> dict[str, Decimal]:
        return _decimal_mapping(self.raw.get("thresholds", {}))

    @property
    def enabled(self) -> bool:
        return bool(self.review.get("enabled", True))

    @property
    def use_mock_llm_summary(self) -> bool:
        return bool(self.review.get("use_mock_llm_summary", True))

    @property
    def prediction_horizon_days(self) -> int:
        return int(self.review.get("prediction_horizon_days", 1))

    @property
    def default_account_type(self) -> str:
        return str(self.review.get("default_account_type", "ai_simulation")).upper()

    def validate(self) -> None:
        weights = self.scoring
        required = {
            "prediction_accuracy_weight",
            "order_price_quality_weight",
            "portfolio_performance_weight",
            "risk_control_weight",
        }
        missing = required.difference(weights)
        if missing:
            raise ReviewConfigError(f"Missing review scoring weights: {sorted(missing)}")

        total_weight = sum(weights[key] for key in required)
        if total_weight != Decimal("1.00"):
            raise ReviewConfigError("Review scoring weights must sum to 1.00.")

        if self.prediction_horizon_days <= 0:
            raise ReviewConfigError("prediction_horizon_days must be greater than 0.")

        if self.thresholds.get("max_acceptable_drawdown_percent", Decimal("0")) <= 0:
            raise ReviewConfigError("max_acceptable_drawdown_percent must be greater than 0.")

        models = get_app_config().config_files.get("models", {}).get("llm", {})
        if bool(models.get("mock_only", True)) is not True:
            raise ReviewConfigError("Phase 11 requires LLM mock_only mode.")

        providers = models.get("providers", {})
        real_enabled = [
            provider_name
            for provider_name, provider_config in providers.items()
            if provider_name != "mock"
            and isinstance(provider_config, dict)
            and bool(provider_config.get("enabled", False))
        ]
        if real_enabled:
            raise ReviewConfigError(f"Real LLM providers must remain disabled: {real_enabled}")

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "use_mock_llm_summary": self.use_mock_llm_summary,
            "prediction_horizon_days": self.prediction_horizon_days,
            "default_account_type": self.default_account_type,
            "scoring": {key: float(value) for key, value in self.scoring.items()},
            "order_price_quality": {
                key: float(value) for key, value in self.order_price_quality.items()
            },
            "thresholds": {key: float(value) for key, value in self.thresholds.items()},
            "llm_mode": "mock_only",
            "data_source_mode": "mock_provider_only",
        }


def load_review_config() -> ReviewConfig:
    loaded = get_app_config().config_files.get("review")
    if not loaded:
        loaded = _read_review_yaml()
    merged = _deep_merge(DEFAULT_REVIEW_CONFIG, loaded or {})
    config = ReviewConfig(raw=merged)
    config.validate()
    return config


def _read_review_yaml() -> dict[str, Any]:
    path = CONFIG_DIR / "review.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file) or {}
    if not isinstance(loaded, dict):
        raise ReviewConfigError("config/review.yaml must contain a mapping.")
    return loaded


def _deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(default)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _decimal_mapping(config: dict[str, Any]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for key, value in config.items():
        if isinstance(value, bool):
            continue
        try:
            result[key] = Decimal(str(value)).quantize(Decimal("0.01"))
        except Exception as exc:
            raise ReviewConfigError(f"Invalid decimal config value for {key}.") from exc
    return result
