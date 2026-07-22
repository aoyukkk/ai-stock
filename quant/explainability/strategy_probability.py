from __future__ import annotations

from dataclasses import dataclass

from entry_timing.strategy import StrategyFeatures


STRATEGY_PROBABILITY_VERSION = "strategy_probability_v3_shadow_2"
PROBABILITY_KEYS = (
    "TREND_BREAKOUT",
    "STRONG_PULLBACK",
    "SECTOR_RESONANCE",
    "OVERSOLD_REBOUND",
    "OPEN_SET",
)


@dataclass(frozen=True)
class StrategyProbabilityResult:
    strategy_probability: dict[str, float]
    primary_strategy: str
    classification_status: str
    evidence_scores: dict[str, float]
    version: str = STRATEGY_PROBABILITY_VERSION


class StrategyProbabilityClassifier:
    """Independent shadow classifier; it does not alter the V2.2 classifier."""

    def classify(self, features: StrategyFeatures) -> StrategyProbabilityResult:
        if features.close is None or features.data_quality_score < 35:
            probabilities = {
                "TREND_BREAKOUT": 0.075,
                "STRONG_PULLBACK": 0.075,
                "SECTOR_RESONANCE": 0.075,
                "OVERSOLD_REBOUND": 0.075,
                "OPEN_SET": 0.7,
            }
            return StrategyProbabilityResult(probabilities, "OPEN_SET", "OPEN_SET", {})

        evidence = {
            "TREND_BREAKOUT": self._trend_breakout(features),
            "STRONG_PULLBACK": self._strong_pullback(features),
            "SECTOR_RESONANCE": self._sector_resonance(features),
            "OVERSOLD_REBOUND": self._oversold_rebound(features),
        }
        best_fit = max(evidence.values())
        evidence["OPEN_SET"] = max(12.0, 100.0 - best_fit)
        probabilities = _normalize({key: max(1.0, evidence[key]) for key in PROBABILITY_KEYS})
        best_known = max(PROBABILITY_KEYS[:-1], key=lambda key: probabilities[key])
        open_set = probabilities["OPEN_SET"] >= probabilities[best_known] or probabilities[best_known] < 0.22
        return StrategyProbabilityResult(
            strategy_probability=probabilities,
            primary_strategy="OPEN_SET" if open_set else best_known,
            classification_status="OPEN_SET" if open_set else "PROBABILISTIC",
            evidence_scores={key: round(value, 6) for key, value in evidence.items()},
        )

    @staticmethod
    def _trend_breakout(f: StrategyFeatures) -> float:
        checks = (
            None not in (f.ma5, f.ma10, f.ma20) and f.ma5 >= f.ma10 >= f.ma20,
            f.ma20_slope is not None and f.ma20_slope > 0,
            f.distance_20d_high is not None and f.distance_20d_high <= 0.05,
            f.volume_ratio is not None and f.volume_ratio >= 1.2,
            f.return_5d is not None and f.return_5d <= 0.15,
            not set(f.risk_flags) & {"MOMENTUM_EXHAUSTION", "DISTRIBUTION_RISK"},
        )
        return _fit(checks, f, regime_bonus=f.market_emotion_state == "GREEN")

    @staticmethod
    def _strong_pullback(f: StrategyFeatures) -> float:
        checks = (
            f.ma20_slope is not None and f.ma20_slope > 0,
            f.recent_drawdown is not None and 0.02 <= f.recent_drawdown <= 0.12,
            f.pullback_volume_ratio is not None and f.pullback_volume_ratio <= 1.0,
            f.close is not None and f.ma20 is not None and f.close >= f.ma20,
            "DISTRIBUTION_RISK" not in f.risk_flags,
        )
        return _fit(checks, f, regime_bonus=f.market_emotion_state in {"GREEN", "YELLOW"})

    @staticmethod
    def _sector_resonance(f: StrategyFeatures) -> float:
        checks = (
            f.sector_score is not None and f.sector_score >= 60,
            f.sector_breadth is None or f.sector_breadth >= 50,
            f.stock_relative_strength is not None and f.stock_relative_strength > 0,
            f.sector_rank_percentile is None or f.sector_rank_percentile <= 40,
        )
        return _fit(checks, f, regime_bonus=f.market_emotion_state != "RED")

    @staticmethod
    def _oversold_rebound(f: StrategyFeatures) -> float:
        checks = (
            f.rsi14 is not None and f.rsi14 <= 35,
            f.return_5d is not None and f.return_5d <= -0.06,
            f.recent_drawdown is not None and f.recent_drawdown >= 0.08,
            f.reversal_confirmation is True,
        )
        return _fit(checks, f, regime_bonus=f.market_regime in {"PANIC_AND_REPAIR", "WEAK_REBOUND", "REPAIR"})


def _fit(checks: tuple[bool, ...], features: StrategyFeatures, *, regime_bonus: bool) -> float:
    pattern = sum(bool(item) for item in checks) / len(checks) * 80.0
    quality = max(0.0, min(100.0, features.data_quality_score)) * 0.1
    return min(100.0, pattern + quality + (10.0 if regime_bonus else 0.0))


def _normalize(values: dict[str, float]) -> dict[str, float]:
    total = sum(values.values())
    probabilities = {key: value / total for key, value in values.items()}
    # Make the serialized contract add exactly to one despite rounding.
    rounded = {key: round(probabilities[key], 6) for key in PROBABILITY_KEYS}
    rounded["OPEN_SET"] = round(rounded["OPEN_SET"] + (1.0 - sum(rounded.values())), 6)
    return rounded
