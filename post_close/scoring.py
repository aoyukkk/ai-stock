from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from math import prod
from statistics import fmean, pstdev
from typing import Any, Iterable, Mapping


class ScoringProfile(StrEnum):
    TUSHARE_BASELINE_V1 = "TUSHARE_BASELINE_V1"
    IFIND_SHADOW_V1 = "IFIND_SHADOW_V1"
    IFIND_ENHANCED_V1 = "IFIND_ENHANCED_V1"


@dataclass(frozen=True)
class CrossProviderFeatureRule:
    primary_provider: str
    validation_provider: str
    scoring_usage: str
    comparison_usage: str
    unit: str
    timestamp_semantics: str


class CrossProviderFeaturePolicy:
    def __init__(self, values: Mapping[str, Mapping[str, Any]]) -> None:
        self.rules = {name: CrossProviderFeatureRule(**dict(rule)) for name, rule in values.items()}

    def rule(self, field: str) -> CrossProviderFeatureRule:
        if field not in self.rules:
            raise KeyError(f"CROSS_PROVIDER_FEATURE_NOT_DECLARED:{field}")
        return self.rules[field]

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {name: asdict(rule) for name, rule in self.rules.items()}


@dataclass(frozen=True)
class IFindDataQuality:
    freshness_score: float
    coverage_score: float
    minute_completeness_score: float
    consistency_score: float
    timestamp_quality_score: float

    @property
    def coefficient(self) -> float:
        values = [max(0.0, min(1.0, value)) for value in asdict(self).values()]
        return max(0.0, min(1.0, prod(values)))


@dataclass(frozen=True)
class MinutePoint:
    close: float
    high: float
    low: float
    volume: float
    amount: float


@dataclass(frozen=True)
class IFindEodFeatures:
    stock_return: float
    index_return: float
    close_position: float
    close_vs_vwap: float
    last_5m_return: float
    last_15m_return: float
    last_30m_return: float
    tail_volume_share: float
    intraday_max_drawdown: float
    recovery_ratio: float
    minute_volatility: float
    minute_completeness: float
    market_regime: str = "DIVERGENT"


@dataclass(frozen=True)
class EnhancementScore:
    base_score: float
    ifind_eod_score: float | None
    overlay_delta: float
    enhanced_score: float
    component_scores: dict[str, float | None]
    data_quality_coefficient: float
    scoring_profile: ScoringProfile
    fallback_reason: str | None


class IFindEodEnhancementEngine:
    COMPONENTS = (
        "relative_strength", "close_quality", "tail_strength", "intraday_stability",
        "liquidity_confirmation", "market_regime_fit",
    )

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.weights = {name: float(value) for name, value in self.config.get("component_weights", {}).items()}
        if set(self.weights) != set(self.COMPONENTS) or abs(sum(self.weights.values()) - 1.0) > 1e-9:
            raise ValueError("IFIND_COMPONENT_WEIGHTS_INVALID")
        overlay = self.config.get("overlay", {})
        self.scale = float(overlay.get("scale", 0.12))
        self.minimum = float(overlay.get("minimum", -6))
        self.maximum = float(overlay.get("maximum", 6))
        self.snapshot_minimum = float(overlay.get("snapshot_only_minimum", -2))
        self.snapshot_maximum = float(overlay.get("snapshot_only_maximum", 2))

    def score(
        self,
        base_score: float,
        features: IFindEodFeatures | None,
        quality: IFindDataQuality | None,
        *,
        dual_source_status: str = "NOT_COMPARABLE",
        minute_available: bool = True,
    ) -> EnhancementScore:
        base = _clip(float(base_score), 0, 100)
        if features is None or quality is None:
            return self._fallback(base, "IFIND_UNAVAILABLE")
        if dual_source_status == "MATERIAL_CONFLICT":
            return self._fallback(base, "MATERIAL_CONFLICT", profile=ScoringProfile.IFIND_SHADOW_V1)

        components = self.component_scores(features)
        ifind_score = sum(components[name] * self.weights[name] for name in self.COMPONENTS)
        raw_delta = (ifind_score - 50.0) * self.scale
        lower, upper = (self.minimum, self.maximum) if minute_available else (self.snapshot_minimum, self.snapshot_maximum)
        delta = _clip(raw_delta, lower, upper) * quality.coefficient
        return EnhancementScore(
            base_score=base,
            ifind_eod_score=round(_clip(ifind_score, 0, 100), 6),
            overlay_delta=round(delta, 6),
            enhanced_score=round(_clip(base + delta, 0, 100), 6),
            component_scores={name: round(value, 6) for name, value in components.items()},
            data_quality_coefficient=round(quality.coefficient, 10),
            scoring_profile=ScoringProfile.IFIND_SHADOW_V1,
            fallback_reason=None,
        )

    def component_scores(self, value: IFindEodFeatures) -> dict[str, float]:
        excess_return = value.stock_return - value.index_return
        relative_strength = _clip(50 + excess_return * 500, 0, 100)
        close_quality = fmean((_clip(value.close_position * 100, 0, 100), _clip(50 + value.close_vs_vwap * 500, 0, 100)))
        tail_strength = fmean((
            _clip(50 + value.last_5m_return * 800, 0, 100),
            _clip(50 + value.last_15m_return * 500, 0, 100),
            _clip(50 + value.last_30m_return * 350, 0, 100),
            _clip(value.tail_volume_share * 300, 0, 100),
        ))
        intraday_stability = fmean((
            _clip(100 - value.intraday_max_drawdown * 800, 0, 100),
            _clip(value.recovery_ratio * 100, 0, 100),
            _clip(100 - value.minute_volatility * 1500, 0, 100),
        ))
        liquidity = _clip(value.minute_completeness * 100, 0, 100)
        regime = self._regime_score(value.market_regime, excess_return, value.minute_volatility)
        return {
            "relative_strength": relative_strength,
            "close_quality": close_quality,
            "tail_strength": tail_strength,
            "intraday_stability": intraday_stability,
            "liquidity_confirmation": liquidity,
            "market_regime_fit": regime,
        }

    @staticmethod
    def features_from_minutes(points: Iterable[MinutePoint], *, stock_return: float, index_return: float, expected_count: int, market_regime: str = "DIVERGENT") -> IFindEodFeatures | None:
        rows = list(points)
        if len(rows) < 2:
            return None
        closes = [row.close for row in rows]
        highs = [row.high for row in rows]
        lows = [row.low for row in rows]
        volumes = [max(0.0, row.volume) for row in rows]
        amounts = [max(0.0, row.amount) for row in rows]
        total_volume = sum(volumes)
        vwap = sum(amounts) / total_volume if total_volume > 0 and sum(amounts) > 0 else fmean(closes)
        returns = [(current / previous - 1) for previous, current in zip(closes, closes[1:]) if previous]
        peak = closes[0]
        max_drawdown = 0.0
        trough = closes[0]
        for close in closes:
            peak = max(peak, close)
            if peak:
                max_drawdown = max(max_drawdown, (peak - close) / peak)
            trough = min(trough, close)
        recovery = (closes[-1] - trough) / max(peak - trough, 1e-9)
        day_high, day_low = max(highs), min(lows)
        close_position = (closes[-1] - day_low) / max(day_high - day_low, 1e-9)
        return IFindEodFeatures(
            stock_return=stock_return,
            index_return=index_return,
            close_position=close_position,
            close_vs_vwap=(closes[-1] / vwap - 1) if vwap else 0.0,
            last_5m_return=_window_return(closes, 5),
            last_15m_return=_window_return(closes, 15),
            last_30m_return=_window_return(closes, 30),
            tail_volume_share=sum(volumes[-30:]) / max(total_volume, 1e-9),
            intraday_max_drawdown=max_drawdown,
            recovery_ratio=_clip(recovery, 0, 1),
            minute_volatility=pstdev(returns) if len(returns) > 1 else 0.0,
            minute_completeness=min(1.0, len(rows) / max(1, expected_count)),
            market_regime=market_regime,
        )

    @staticmethod
    def features_from_snapshot(*, stock_return: float, index_return: float, close: float, high: float, low: float, market_regime: str = "DIVERGENT") -> IFindEodFeatures | None:
        if close <= 0 or high <= 0 or low <= 0 or high < low:
            return None
        close_position = 0.5 if high == low else _clip((close - low) / (high - low), 0, 1)
        return IFindEodFeatures(
            stock_return=stock_return, index_return=index_return, close_position=close_position,
            close_vs_vwap=0.0, last_5m_return=0.0, last_15m_return=0.0, last_30m_return=0.0,
            tail_volume_share=1 / 6, intraday_max_drawdown=0.0625, recovery_ratio=0.5,
            minute_volatility=1 / 30, minute_completeness=0.5, market_regime=market_regime,
        )

    @staticmethod
    def _regime_score(regime: str, excess_return: float, volatility: float) -> float:
        name = str(regime or "DIVERGENT").upper()
        defensive_bonus = 15 if name in {"PANIC", "BROAD_DECLINE"} and excess_return > 0 else 0
        chase_penalty = 15 if name in {"LOW_VOLUME", "BROAD_RALLY"} and volatility > 0.02 else 0
        return _clip(50 + excess_return * 300 + defensive_bonus - chase_penalty, 0, 100)

    @staticmethod
    def _fallback(base: float, reason: str, *, profile: ScoringProfile = ScoringProfile.TUSHARE_BASELINE_V1) -> EnhancementScore:
        return EnhancementScore(base, None, 0.0, base, {name: None for name in IFindEodEnhancementEngine.COMPONENTS}, 0.0, profile, reason)


def _window_return(values: list[float], count: int) -> float:
    if len(values) < 2:
        return 0.0
    start = values[max(0, len(values) - count - 1)]
    return values[-1] / start - 1 if start else 0.0


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
