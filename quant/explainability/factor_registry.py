from __future__ import annotations

from dataclasses import dataclass


MOMENTUM = "MOMENTUM"
VOLUME_CAPITAL = "VOLUME_CAPITAL"
POSITION_TREND = "POSITION_TREND"
SENTIMENT_REGIME = "SENTIMENT_REGIME"
FUNDAMENTAL = "FUNDAMENTAL"
RISK_LIQUIDITY = "RISK_LIQUIDITY"

FACTOR_FAMILIES = (
    MOMENTUM,
    VOLUME_CAPITAL,
    POSITION_TREND,
    SENTIMENT_REGIME,
    FUNDAMENTAL,
    RISK_LIQUIDITY,
)

# This is an explanatory projection of the frozen Quant 25/25/20/15/15
# baseline. It is not a second scoring configuration.
BASELINE_QUANT_WEIGHTS = {
    POSITION_TREND: 0.25,
    VOLUME_CAPITAL: 0.25,
    SENTIMENT_REGIME: 0.20,
    MOMENTUM: 0.15,
    RISK_LIQUIDITY: 0.15,
    FUNDAMENTAL: 0.0,
}


@dataclass(frozen=True)
class FactorDefinition:
    raw_metric: str
    subfactor: str
    factor_family: str


_DEFINITIONS = (
    FactorDefinition("return_1d", "SHORT_RETURN", MOMENTUM),
    FactorDefinition("return_5d", "SHORT_RETURN", MOMENTUM),
    FactorDefinition("return_10d", "MEDIUM_RETURN", MOMENTUM),
    FactorDefinition("momentum_score", "QUANT_MOMENTUM", MOMENTUM),
    FactorDefinition("volume_ratio", "VOLUME_CONFIRMATION", VOLUME_CAPITAL),
    FactorDefinition("amount_ratio", "CAPITAL_ACTIVITY", VOLUME_CAPITAL),
    FactorDefinition("capital_score", "QUANT_CAPITAL", VOLUME_CAPITAL),
    FactorDefinition("technical_score", "TREND_STRUCTURE", POSITION_TREND),
    FactorDefinition("position_score", "PRICE_POSITION", POSITION_TREND),
    FactorDefinition("pullback_score", "PULLBACK_QUALITY", POSITION_TREND),
    FactorDefinition("emotion_score", "QUANT_EMOTION", SENTIMENT_REGIME),
    FactorDefinition("market_emotion_score", "MARKET_EMOTION", SENTIMENT_REGIME),
    FactorDefinition("sector_strength", "SECTOR_RESONANCE", SENTIMENT_REGIME),
    FactorDefinition("flash_score", "BUSINESS_AND_EVENT_QUALITY", FUNDAMENTAL),
    FactorDefinition("business_quality_score", "BUSINESS_QUALITY", FUNDAMENTAL),
    FactorDefinition("risk_score", "QUANT_RISK", RISK_LIQUIDITY),
    FactorDefinition("liquidity_score", "LIQUIDITY", RISK_LIQUIDITY),
    FactorDefinition("atr_ratio", "VOLATILITY", RISK_LIQUIDITY),
)

FACTOR_REGISTRY = {item.raw_metric: item for item in _DEFINITIONS}


def definition_for(raw_metric: str) -> FactorDefinition:
    try:
        return FACTOR_REGISTRY[raw_metric]
    except KeyError as exc:
        raise ValueError(f"UNKNOWN_FACTOR_METRIC:{raw_metric}") from exc
