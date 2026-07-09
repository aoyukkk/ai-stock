from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.core.config import get_app_config
from screening.exceptions import LightScreeningConfigError


DEFAULT_LIGHT_SCREENING_CONFIG = {
    "enabled": True,
    "provider": "mock",
    "model": "mock-chat",
    "batch_size": 20,
    "output_top_n": 50,
    "min_confidence": 0.50,
    "persist_default": False,
    "score_weights": {
        "quant_signal": 0.30,
        "event_catalyst": 0.20,
        "sector_strength": 0.15,
        "order_friendliness": 0.15,
        "liquidity": 0.10,
        "risk_penalty": 0.10,
    },
}


@dataclass(frozen=True)
class LightScreeningConfig:
    raw: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", True))

    @property
    def provider(self) -> str:
        return str(self.raw.get("provider", "mock"))

    @property
    def model(self) -> str:
        return str(self.raw.get("model", "mock-chat"))

    @property
    def batch_size(self) -> int:
        return int(self.raw.get("batch_size", 20))

    @property
    def output_top_n(self) -> int:
        stock_scan = get_app_config().config_files.get("stock_scan", {}).get("stock_scan", {})
        return int(self.raw.get("output_top_n") or stock_scan.get("committee_analysis_top_n", 50))

    @property
    def min_confidence(self) -> Decimal:
        return Decimal(str(self.raw.get("min_confidence", "0.50")))

    @property
    def persist_default(self) -> bool:
        return bool(self.raw.get("persist_default", False))

    @property
    def score_weights(self) -> dict[str, Decimal]:
        raw_weights = self.raw.get("score_weights", {})
        return {
            key: Decimal(str(raw_weights.get(key, DEFAULT_LIGHT_SCREENING_CONFIG["score_weights"][key])))
            for key in (
                "quant_signal",
                "event_catalyst",
                "sector_strength",
                "order_friendliness",
                "liquidity",
                "risk_penalty",
            )
        }

    def validate_weights(self) -> None:
        total = sum(self.score_weights.values(), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("0.0001"):
            raise LightScreeningConfigError(
                f"Light screening score weights must sum to 1.0, got {total}."
            )

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "batch_size": self.batch_size,
            "output_top_n": self.output_top_n,
            "min_confidence": float(self.min_confidence),
            "persist_default": self.persist_default,
            "score_weights": {key: float(value) for key, value in self.score_weights.items()},
        }


def load_light_screening_config() -> LightScreeningConfig:
    models = get_app_config().config_files.get("models", {})
    raw = DEFAULT_LIGHT_SCREENING_CONFIG | models.get("llm_light_screening", {})
    return LightScreeningConfig(raw=raw)
