from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from quant.explainability.factor_lineage import lineage_for_metrics
from quant.explainability.factor_registry import (
    BASELINE_QUANT_WEIGHTS,
    FACTOR_FAMILIES,
    FUNDAMENTAL,
    MOMENTUM,
    POSITION_TREND,
    RISK_LIQUIDITY,
    SENTIMENT_REGIME,
    VOLUME_CAPITAL,
)


ATTRIBUTION_VERSION = "factor_attribution_v3_shadow_1"
OPAQUE_LLM_CONTRIBUTION = "OPAQUE_LLM_CONTRIBUTION"


@dataclass(frozen=True)
class FactorAttributionRecord:
    stock_code: str
    trade_date: date
    factor_family: str
    raw_signal: dict[str, Any]
    normalized_score: float
    score_contribution: float
    gate_contribution: float
    rank_contribution: float
    interaction_note: str
    lineage: list[dict[str, Any]]
    version: str = ATTRIBUTION_VERSION


class FactorAttributionEngine:
    """Project frozen Quant components into stable explanatory families."""

    def attribute(
        self,
        *,
        stock_code: str,
        trade_date: date,
        technical_score: float | None,
        capital_score: float | None,
        emotion_score: float | None,
        momentum_score: float | None,
        risk_score: float | None,
        flash_score: float | None = None,
        raw_metrics: dict[str, float | None] | None = None,
        gate_contributions: dict[str, float] | None = None,
    ) -> list[FactorAttributionRecord]:
        raw_metrics = raw_metrics or {}
        gate_contributions = gate_contributions or {}
        family_inputs: dict[str, tuple[float | None, dict[str, Any]]] = {
            MOMENTUM: (momentum_score, {"momentum_score": momentum_score}),
            VOLUME_CAPITAL: (capital_score, {"capital_score": capital_score}),
            POSITION_TREND: (technical_score, {"technical_score": technical_score}),
            SENTIMENT_REGIME: (emotion_score, {"emotion_score": emotion_score}),
            FUNDAMENTAL: (flash_score, {"flash_score": flash_score}),
            RISK_LIQUIDITY: (risk_score, {"risk_score": risk_score}),
        }
        for metric, value in raw_metrics.items():
            lineage = lineage_for_metrics([metric])
            if lineage:
                family_inputs[lineage[0].factor_family][1][metric] = value

        records: list[FactorAttributionRecord] = []
        for family in FACTOR_FAMILIES:
            score, signals = family_inputs[family]
            normalized = _bounded(score)
            weight = BASELINE_QUANT_WEIGHTS[family]
            score_contribution = normalized * weight
            gate_contribution = float(gate_contributions.get(family, 0.0))
            if family == FUNDAMENTAL:
                signals["contribution_marker"] = OPAQUE_LLM_CONTRIBUTION
                note = (
                    f"{OPAQUE_LLM_CONTRIBUTION}: Flash/Pro output is not decomposed into "
                    "bottom-level factors; shadow context only, with no production Quant weight "
                    "or ranking authority."
                )
            elif gate_contribution:
                note = "Quant contribution combined with a separate Admission V3 shadow gate effect."
            else:
                note = "Frozen Quant baseline contribution; no shadow reweighting applied."
            records.append(
                FactorAttributionRecord(
                    stock_code=stock_code,
                    trade_date=trade_date,
                    factor_family=family,
                    raw_signal=signals,
                    normalized_score=round(normalized, 6),
                    score_contribution=round(score_contribution, 6),
                    gate_contribution=round(gate_contribution, 6),
                    rank_contribution=round(score_contribution + gate_contribution, 6),
                    interaction_note=note,
                    lineage=[item.as_dict() for item in lineage_for_metrics(list(signals))],
                )
            )
        return records


def _bounded(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(100.0, float(value)))
