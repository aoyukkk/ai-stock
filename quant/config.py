from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.core.config import get_app_config


DEFAULT_QUANT_CONFIG = {
    "quant_factor": {
        "factor_version": "v0.3-phase4",
        "weights": {
            "technical": 0.25,
            "capital": 0.25,
            "emotion": 0.20,
            "momentum": 0.15,
            "risk": 0.15,
        },
        "normalize": {
            "method": "percentile",
            "score_range": {"min": 0, "max": 100},
        },
        "filter": {
            "exclude_st": True,
            "exclude_suspended": True,
            "min_listing_days": 60,
            "min_daily_amount": 50000000,
        },
        "validation": {
            "require_weight_sum_one": True,
            "weight_tolerance": 0.0001,
            "prevent_future_data_leakage": True,
        },
    },
    "technical_factor": {
        "ma": {"enabled": True, "windows": [5, 20, 60]},
        "rsi": {"enabled": True, "window": 14},
        "atr": {"enabled": True, "window": 14},
        "vwap": {"enabled": True},
        "price_adjustment": {
            "enabled": False,
            "mode": "RAW",
            "point_in_time_required": True,
            "fallback_to_raw": True,
            "debug_ab": True,
        },
    },
    "capital_factor": {
        "main_money_flow": {"enabled": True},
        "volume_ratio": {"enabled": True, "window": 5},
        "turnover_rate": {"enabled": True},
    },
    "emotion_factor": {
        "limit_up_count": {"enabled": True},
        "limit_down_count": {"enabled": True},
    },
    "momentum_factor": {"r5_weight": 0.60, "r20_weight": 0.40},
    "risk_factor": {
        "volatility_window": 20,
        "drawdown_window": 20,
        "price_limit": {
            "enabled": False,
            "near_limit_percent": 0.01,
            "consecutive_window": 5,
            "tick_size": 0.01,
        },
        "internal_weights": {
            "volatility": 0.30,
            "drawdown": 0.25,
            "liquidity": 0.15,
            "financial": 0.15,
            "price_limit": 0.15,
        },
    },
}


@dataclass(frozen=True)
class QuantConfig:
    raw: dict[str, Any]

    @property
    def quant_factor(self) -> dict[str, Any]:
        return self.raw.get("quant_factor", {})

    @property
    def weights(self) -> dict[str, Decimal]:
        raw_weights = self.quant_factor.get("weights", {})
        return {
            key: Decimal(str(raw_weights.get(key, DEFAULT_QUANT_CONFIG["quant_factor"]["weights"][key])))
            for key in ("technical", "capital", "emotion", "momentum", "risk")
        }

    @property
    def factor_version(self) -> str:
        return str(self.quant_factor.get("factor_version") or "v0.3-phase4")

    @property
    def top_q_default(self) -> int:
        stock_scan = get_app_config().config_files.get("stock_scan", {}).get("stock_scan", {})
        return int(stock_scan.get("quant_top_n", 500))

    @property
    def weight_tolerance(self) -> Decimal:
        validation = self.quant_factor.get("validation", {})
        return Decimal(str(validation.get("weight_tolerance", "0.0001")))

    def summary(self) -> dict[str, Any]:
        return {
            "weights": {key: float(value) for key, value in self.weights.items()},
            "normalize": self.quant_factor.get("normalize", {}),
            "filter": self.quant_factor.get("filter", {}),
            "factor_version": self.factor_version,
        }


def load_quant_config() -> QuantConfig:
    config_files = get_app_config().config_files
    raw = DEFAULT_QUANT_CONFIG | config_files.get("quant_factor", {})
    return QuantConfig(raw=raw)
