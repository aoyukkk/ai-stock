from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


STRATEGIES = (
    "TREND_BREAKOUT", "STRONG_PULLBACK", "SECTOR_RESONANCE", "OVERSOLD_REBOUND",
)


@dataclass(frozen=True)
class StrategyFeatures:
    close: float | None = None
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    ma20_slope: float | None = None
    ma60_slope: float | None = None
    return_1d: float | None = None
    return_5d: float | None = None
    return_10d: float | None = None
    distance_20d_high: float | None = None
    recent_drawdown: float | None = None
    volume_ratio: float | None = None
    pullback_volume_ratio: float | None = None
    rsi14: float | None = None
    atr_ratio: float | None = None
    sector_score: float | None = None
    sector_breadth: float | None = None
    stock_relative_strength: float | None = None
    sector_rank_percentile: float | None = None
    market_regime: str = "UNKNOWN"
    market_emotion_state: str = "RED"
    reversal_confirmation: bool | None = None
    risk_flags: list[str] = field(default_factory=list)
    data_quality_score: float = 0


@dataclass(frozen=True)
class StrategyClassification:
    strategy_id: str
    primary_strategy: str
    alternative_strategy_ids: list[str]
    strategy_fit_score: float
    pattern_fit_score: float
    regime_compatibility_score: float
    sector_compatibility_score: float | None
    data_quality_score: float
    strategy_confidence: float
    matched_conditions: list[str]
    failed_conditions: list[str]
    classification_status: str
    classifier_version: str
    details: dict[str, Any]


class ShortTermStrategyClassifier:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.version = str(config.get("classifier_version") or "short_strategy_classifier_v1")

    def classify(self, features: StrategyFeatures) -> StrategyClassification:
        if features.close is None or features.data_quality_score < 35:
            return self._unclassified(features, "DATA_INSUFFICIENT", ["CRITICAL_FEATURES_MISSING"])
        evaluations = {
            "TREND_BREAKOUT": self._trend_breakout(features),
            "STRONG_PULLBACK": self._strong_pullback(features),
            "SECTOR_RESONANCE": self._sector_resonance(features),
            "OVERSOLD_REBOUND": self._oversold_rebound(features),
        }
        matched = [(strategy, result) for strategy, result in evaluations.items() if result[0]]
        if not matched:
            best_strategy, best = max(evaluations.items(), key=lambda item: item[1][1])
            return self._unclassified(features, "UNCLASSIFIED", best[3], alternative=[best_strategy] if best[1] >= 45 else [])
        scored = []
        for strategy, (_, pattern, matched_conditions, failed_conditions) in matched:
            regime = self._regime_compatibility(strategy, features)
            sector = self._sector_compatibility(features)
            fit = self._weighted_available({
                "pattern": (pattern, 0.40), "regime": (regime, 0.25),
                "sector": (sector, 0.20), "data": (features.data_quality_score, 0.15),
            })
            scored.append((strategy, fit, pattern, regime, sector, matched_conditions, failed_conditions))
        scored.sort(key=lambda item: (-item[1], item[0]))
        primary = scored[0]
        alternatives = [item[0] for item in scored[1:]]
        available = sum(value is not None for value in (features.ma20, features.volume_ratio, features.rsi14, features.sector_score, features.sector_breadth))
        confidence = min(100.0, primary[1] * 0.65 + features.data_quality_score * 0.25 + available / 5 * 10)
        status = "MULTIPLE_MATCHES" if alternatives else "MATCHED" if confidence >= 60 else "LOW_CONFIDENCE"
        return StrategyClassification(
            strategy_id=primary[0], primary_strategy=primary[0], alternative_strategy_ids=alternatives,
            strategy_fit_score=round(primary[1], 4), pattern_fit_score=round(primary[2], 4),
            regime_compatibility_score=round(primary[3], 4),
            sector_compatibility_score=round(primary[4], 4) if primary[4] is not None else None,
            data_quality_score=round(features.data_quality_score, 4),
            strategy_confidence=round(confidence, 4), matched_conditions=primary[5],
            failed_conditions=primary[6], classification_status=status,
            classifier_version=self.version,
            details={"concept_component_status": "NOT_AVAILABLE", "evaluated_strategies": list(evaluations)},
        )

    def _trend_breakout(self, f: StrategyFeatures):
        c = self.config["strategies"]["trend_breakout"]
        checks = {
            "MA_STRUCTURE": None not in (f.ma5, f.ma10, f.ma20) and f.ma5 >= f.ma10 >= f.ma20,
            "MA20_SLOPE": f.ma20_slope is not None and f.ma20_slope >= float(c["minimum_ma20_slope"]),
            "NEAR_20D_HIGH": f.distance_20d_high is not None and f.distance_20d_high * 100 <= float(c["maximum_distance_to_20d_high_percent"]),
            "VOLUME_CONFIRMATION": f.volume_ratio is not None and f.volume_ratio >= float(c["minimum_volume_ratio"]),
            "RETURN_NOT_EXTENDED": f.return_5d is not None and f.return_5d * 100 <= float(c["maximum_return_5d_percent"]),
            "SECTOR_NOT_WEAK": f.sector_score is not None and f.sector_score >= float(c["minimum_sector_resonance_score"]),
            "NO_EXHAUSTION": not set(f.risk_flags) & {"MOMENTUM_EXHAUSTION", "DISTRIBUTION_RISK"},
        }
        return self._checks(checks, required=6)

    def _strong_pullback(self, f: StrategyFeatures):
        c = self.config["strategies"]["strong_pullback"]
        trend_score = self._trend_score(f)
        checks = {
            "UPTREND": trend_score >= float(c["minimum_trend_score"]),
            "PULLBACK_RANGE": f.recent_drawdown is not None and float(c["minimum_pullback_percent"]) <= f.recent_drawdown * 100 <= float(c["maximum_pullback_percent"]),
            "PULLBACK_VOLUME_CONTRACTED": f.pullback_volume_ratio is not None and f.pullback_volume_ratio <= float(c["maximum_pullback_volume_ratio"]),
            "ABOVE_MA20": f.close is not None and f.ma20 is not None and f.close >= f.ma20,
            "NO_DISTRIBUTION": "DISTRIBUTION_RISK" not in f.risk_flags,
        }
        return self._checks(checks, required=4)

    def _sector_resonance(self, f: StrategyFeatures):
        c = self.config["strategies"]["sector_resonance"]
        checks = {
            "SECTOR_SCORE": f.sector_score is not None and f.sector_score >= float(c["minimum_sector_score"]),
            "SECTOR_BREADTH": f.sector_breadth is None or f.sector_breadth >= float(c["minimum_sector_breadth"]),
            "RELATIVE_STRENGTH": f.stock_relative_strength is not None and f.stock_relative_strength >= float(c["minimum_stock_relative_strength"]),
            "SECTOR_RANK": f.sector_rank_percentile is None or f.sector_rank_percentile <= float(c["maximum_stock_sector_rank_percentile"]),
        }
        return self._checks(checks, required=3)

    def _oversold_rebound(self, f: StrategyFeatures):
        c = self.config["strategies"]["oversold_rebound"]
        checks = {
            "RSI_OVERSOLD": f.rsi14 is not None and f.rsi14 <= float(c["maximum_rsi14"]),
            "FIVE_DAY_DECLINE": f.return_5d is not None and f.return_5d * 100 <= float(c["maximum_return_5d_percent"]),
            "DRAWDOWN": f.recent_drawdown is not None and f.recent_drawdown * 100 >= float(c["minimum_drawdown_percent"]),
            "REVERSAL_CONFIRMATION": f.reversal_confirmation is True,
        }
        return self._checks(checks, required=4)

    @staticmethod
    def _checks(checks: dict[str, bool], required: int):
        matched = [key for key, value in checks.items() if value]
        failed = [key for key, value in checks.items() if not value]
        score = len(matched) / len(checks) * 100
        return len(matched) >= required, score, matched, failed

    @staticmethod
    def _trend_score(f: StrategyFeatures) -> float:
        values = [
            30 if f.ma20_slope is not None and f.ma20_slope > 0 else 0,
            25 if None not in (f.ma10, f.ma20) and f.ma10 >= f.ma20 else 0,
            25 if f.close is not None and f.ma20 is not None and f.close >= f.ma20 else 0,
            20 if f.ma60_slope is not None and f.ma60_slope >= 0 else 0,
        ]
        return sum(values)

    @staticmethod
    def _regime_compatibility(strategy: str, f: StrategyFeatures) -> float:
        state = f.market_emotion_state
        if strategy == "OVERSOLD_REBOUND":
            if f.market_regime in {"PANIC_AND_REPAIR", "WEAK_REBOUND"}: return 85
            return 45 if state == "GREEN" else 55 if state == "YELLOW" else 35
        if state == "GREEN": return 90
        if state == "YELLOW": return 70 if strategy in {"STRONG_PULLBACK", "SECTOR_RESONANCE"} else 55
        return 20

    @staticmethod
    def _sector_compatibility(f: StrategyFeatures) -> float | None:
        if f.sector_score is None:
            return None
        values = [f.sector_score]
        if f.sector_breadth is not None: values.append(f.sector_breadth)
        if f.stock_relative_strength is not None: values.append(max(0, min(100, 50 + f.stock_relative_strength * 10)))
        return sum(values) / len(values)

    @staticmethod
    def _weighted_available(values: dict[str, tuple[float | None, float]]) -> float:
        available = [(value, weight) for value, weight in values.values() if value is not None]
        total_weight = sum(weight for _, weight in available)
        return sum(value * weight for value, weight in available) / total_weight if total_weight else 0

    def _unclassified(self, f: StrategyFeatures, status: str, failed: list[str], alternative: list[str] | None = None):
        return StrategyClassification(
            strategy_id="UNCLASSIFIED", primary_strategy="UNCLASSIFIED",
            alternative_strategy_ids=alternative or [], strategy_fit_score=0,
            pattern_fit_score=0, regime_compatibility_score=0,
            sector_compatibility_score=f.sector_score, data_quality_score=f.data_quality_score,
            strategy_confidence=min(49, f.data_quality_score * 0.45), matched_conditions=[],
            failed_conditions=failed, classification_status=status, classifier_version=self.version,
            details={"concept_component_status": "NOT_AVAILABLE"},
        )
