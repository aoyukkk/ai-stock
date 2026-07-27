from __future__ import annotations

from dataclasses import dataclass
from typing import Any


VERSION = "RISK_V2_1_SHADOW"
WEIGHTS = {
    "risk_v2_frozen": 0.40,
    "max_drawdown_20d": 0.15,
    "downside_volatility_20d": 0.15,
    "chip_crowding": 0.15,
    "event_risk": 0.15,
}


def _clip(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _inverse_linear(value: float, safe: float, danger: float) -> float:
    if danger <= safe:
        raise ValueError("RISK_V2_1_INVALID_NORMALIZATION_RANGE")
    return _clip((danger - float(value)) / (danger - safe) * 100)


@dataclass(frozen=True)
class RiskV21Input:
    risk_v2_frozen: float
    max_drawdown_20d: float | None
    downside_volatility_20d: float | None
    chip_profit_ratio: float | None
    close_to_chip_cost: float | None
    unlock_risk: float | None
    reduction_risk: float | None
    pledge_risk: float | None
    major_financial_event_risk: float | None
    pipeline_error: str | None = None


def calculate_risk_v2_1(value: RiskV21Input) -> dict[str, Any]:
    """Research-only health score; missing fields contribute zero, never neutral 50."""

    if value.pipeline_error:
        return {
            "version": VERSION,
            "status": "DATA_PIPELINE_ERROR",
            "score": None,
            "coverage": 0.0,
            "confidence": "INVALID",
            "missing_components": [],
            "pipeline_error": value.pipeline_error,
            "components": {},
        }

    components: dict[str, dict[str, Any]] = {
        "risk_v2_frozen": {
            "raw_value": value.risk_v2_frozen,
            "score": _clip(value.risk_v2_frozen),
            "direction": "HIGHER_IS_SAFER",
        }
    }
    missing: list[str] = []

    def add(name: str, raw: float | None, score: float | None, direction: str) -> None:
        if raw is None or score is None:
            missing.append(name)
            components[name] = {
                "raw_value": None,
                "score": 0.0,
                "direction": direction,
                "missing_policy": "ZERO_CONTRIBUTION_NO_RENORMALIZATION",
            }
        else:
            components[name] = {
                "raw_value": raw,
                "score": _clip(score),
                "direction": direction,
            }

    add(
        "max_drawdown_20d",
        value.max_drawdown_20d,
        None
        if value.max_drawdown_20d is None
        else _inverse_linear(value.max_drawdown_20d, 0, 30),
        "INVERSE_HIGHER_DRAWDOWN_IS_LESS_SAFE",
    )
    add(
        "downside_volatility_20d",
        value.downside_volatility_20d,
        None
        if value.downside_volatility_20d is None
        else _inverse_linear(value.downside_volatility_20d, 0, 8),
        "INVERSE_HIGHER_DOWNSIDE_VOLATILITY_IS_LESS_SAFE",
    )
    chip_raw = (
        None
        if value.chip_profit_ratio is None or value.close_to_chip_cost is None
        else {
            "chip_profit_ratio": value.chip_profit_ratio,
            "close_to_chip_cost": value.close_to_chip_cost,
        }
    )
    chip_score = None
    if chip_raw is not None:
        profit_health = _inverse_linear(value.chip_profit_ratio, 50, 95)
        cost_health = _inverse_linear(max(value.close_to_chip_cost - 1, 0) * 100, 0, 35)
        chip_score = profit_health * 0.55 + cost_health * 0.45
    add(
        "chip_crowding",
        chip_raw,  # type: ignore[arg-type]
        chip_score,
        "INVERSE_HIGH_PROFIT_RATIO_OR_FAR_ABOVE_COST_IS_LESS_SAFE",
    )
    event_values = (
        value.unlock_risk,
        value.reduction_risk,
        value.pledge_risk,
        value.major_financial_event_risk,
    )
    event_raw = (
        None
        if any(item is None for item in event_values)
        else {
            "unlock_risk": value.unlock_risk,
            "reduction_risk": value.reduction_risk,
            "pledge_risk": value.pledge_risk,
            "major_financial_event_risk": value.major_financial_event_risk,
        }
    )
    event_score = (
        None
        if event_raw is None
        else 100 - max(float(item) for item in event_values if item is not None)
    )
    add(
        "event_risk",
        event_raw,  # type: ignore[arg-type]
        event_score,
        "INVERSE_WORST_EVENT_RISK_BINDS",
    )

    score = sum(
        float(components[name]["score"]) * weight
        for name, weight in WEIGHTS.items()
    )
    observed_weight = sum(
        weight
        for name, weight in WEIGHTS.items()
        if name not in missing
    )
    for name, weight in WEIGHTS.items():
        components[name]["weight"] = weight
        components[name]["contribution"] = round(
            float(components[name]["score"]) * weight, 4
        )
    return {
        "version": VERSION,
        "status": "SHADOW_ONLY",
        "score": round(score, 4),
        "coverage": round(observed_weight, 4),
        "confidence": (
            "HIGH" if observed_weight >= 0.85
            else "MEDIUM" if observed_weight >= 0.70
            else "LOW"
        ),
        "missing_components": missing,
        "missing_policy": "ZERO_CONTRIBUTION_NO_RENORMALIZATION",
        "components": components,
    }
