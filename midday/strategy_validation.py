from __future__ import annotations

from dataclasses import dataclass

from entry_timing.strategy import STRATEGIES, ShortTermStrategyClassifier, StrategyClassification, StrategyFeatures


@dataclass(frozen=True)
class MiddayStrategyValidation:
    strategy_id: str
    base_strategy_id: str
    strategy_source: str
    live_strategy_id: str
    live_strategy_status: str
    live_strategy_fit: float
    live_regime_compatibility_score: float
    live_strategy_fit_delta: float | None
    strategy_still_valid: bool
    classification_status: str


class MiddayStrategyValidator:
    """Preserve a legal daily strategy and only validate it with partial-day facts."""

    def __init__(self, classifier: ShortTermStrategyClassifier) -> None:
        self.classifier = classifier

    def evaluate(self, base_strategy_id: str | None, base_fit: float | None, features: StrategyFeatures) -> tuple[MiddayStrategyValidation, StrategyClassification]:
        live = self.classifier.classify(features)
        base = str(base_strategy_id or "UNCLASSIFIED")
        if base not in STRATEGIES:
            strategy = live.strategy_id
            status = "DATA_INSUFFICIENT" if live.classification_status == "DATA_INSUFFICIENT" else "CONFIRMED" if strategy in STRATEGIES else "INVALIDATED"
            return MiddayStrategyValidation(
                strategy_id=strategy, base_strategy_id="UNCLASSIFIED", strategy_source="LIVE_FALLBACK",
                live_strategy_id=live.strategy_id, live_strategy_status=status,
                live_strategy_fit=float(live.strategy_fit_score), live_regime_compatibility_score=float(live.regime_compatibility_score), live_strategy_fit_delta=None,
                strategy_still_valid=strategy in STRATEGIES and status == "CONFIRMED",
                classification_status=live.classification_status,
            ), live

        if live.classification_status == "DATA_INSUFFICIENT":
            live_fit = None
            regime_fit = 0
            status = "DATA_INSUFFICIENT"
        else:
            matched, live_fit, regime_fit = self._evaluate_base(base, features)
            status = "CONFIRMED" if matched else "WEAKENED" if live_fit >= 45 else "INVALIDATED"
        delta = None if live_fit is None or base_fit is None else round(live_fit - float(base_fit), 4)
        return MiddayStrategyValidation(
            strategy_id=base, base_strategy_id=base, strategy_source="HISTORICAL_PRIMARY",
            live_strategy_id=live.strategy_id, live_strategy_status=status,
            live_strategy_fit=float(live_fit or 0), live_regime_compatibility_score=float(regime_fit if live_fit is not None else 0), live_strategy_fit_delta=delta,
            strategy_still_valid=status in {"CONFIRMED", "WEAKENED"},
            classification_status=f"HISTORICAL_{status}",
        ), live

    def _evaluate_base(self, strategy_id: str, features: StrategyFeatures) -> tuple[bool, float, float]:
        evaluator = {
            "TREND_BREAKOUT": self.classifier._trend_breakout,
            "STRONG_PULLBACK": self.classifier._strong_pullback,
            "SECTOR_RESONANCE": self.classifier._sector_resonance,
            "OVERSOLD_REBOUND": self.classifier._oversold_rebound,
        }[strategy_id]
        matched, pattern, _matched_conditions, _failed_conditions = evaluator(features)
        regime = self.classifier._regime_compatibility(strategy_id, features)
        sector = self.classifier._sector_compatibility(features)
        fit = self.classifier._weighted_available({
            "pattern": (pattern, 0.40), "regime": (regime, 0.25),
            "sector": (sector, 0.20), "data": (features.data_quality_score, 0.15),
        })
        return matched, round(fit, 4), round(regime, 4)
