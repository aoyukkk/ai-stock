from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from entry_timing.engine import EntryTimingAssessment
from entry_timing.strategy import StrategyClassification


@dataclass(frozen=True)
class EntryTimingV2Assessment:
    entry_timing_v2_score: float | None
    component_scores: dict[str, float | None]
    component_coverage: float
    version: str


class EntryTimingV2Engine:
    BASE_MAXIMUMS = {
        "price_position": 25, "pullback_quality": 20, "volume_price_structure": 20,
        "sector_resonance": 15, "liquidity": 10,
    }

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.version = str(config.get("entry_timing_version") or "entry_timing_v2_1")

    def evaluate(self, v1: EntryTimingAssessment, strategy: StrategyClassification) -> EntryTimingV2Assessment:
        raw = {
            "price_position": self._normalize(v1.position_score, 25),
            "pullback_quality": self._normalize(v1.pullback_score, 20),
            "volume_price_structure": self._normalize(v1.volume_price_score, 20),
            "sector_resonance": self._normalize(v1.sector_score, 15) if v1.diagnostics.get("sector_change") is not None else None,
            "strategy_market_fit": strategy.regime_compatibility_score if strategy.classification_status != "DATA_INSUFFICIENT" else None,
            "liquidity": self._normalize(v1.liquidity_score, 10),
        }
        weights = self.config["weights"]
        available_weight = sum(float(weights[key]) for key, value in raw.items() if value is not None)
        score = sum(value * float(weights[key]) for key, value in raw.items() if value is not None) / available_weight if available_weight else None
        return EntryTimingV2Assessment(
            entry_timing_v2_score=round(score, 4) if score is not None else None,
            component_scores={key: round(value, 4) if value is not None else None for key, value in raw.items()},
            component_coverage=round(available_weight, 4), version=self.version,
        )

    @staticmethod
    def _normalize(value: float | None, maximum: float) -> float | None:
        return max(0, min(100, float(value) / maximum * 100)) if value is not None else None
