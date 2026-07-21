from __future__ import annotations

from market_review.schemas import (
    MarketDailySnapshotData,
    MarketOutlookResult,
    MarketRegimeResult,
    ScenarioRule,
)


class MarketRegimeEngine:
    def __init__(self, version: str = "market_regime_v1") -> None:
        self.version = version

    def evaluate(self, snapshot: MarketDailySnapshotData) -> MarketRegimeResult:
        breadth = snapshot.breadth
        turnover = snapshot.turnover
        up_ratio = float(breadth.get("advancing_ratio") or 0)
        down_ratio = float(breadth.get("declining_ratio") or 0)
        average_return = float(breadth.get("equal_weight_return") or 0)
        turnover_change = turnover.get("change_ratio")
        turnover_change = float(turnover_change) if turnover_change is not None else 0.0
        index_returns = [item.change_percent for item in snapshot.indices if item.change_percent is not None]
        index_average = sum(index_returns) / len(index_returns) if index_returns else None
        top_concept = max((item.change_percent for item in snapshot.concepts), default=0.0)

        supporting: list[str] = []
        conflicting: list[str] = []
        score = _clamp(50 + average_return * 1800 + (up_ratio - down_ratio) * 25 + turnover_change * 10)
        if snapshot.data_quality_score < 45 or int(breadth.get("valid_count") or 0) < 100:
            regime = "DATA_INSUFFICIENT"
            supporting.append("snapshot.data_quality_score")
        elif up_ratio >= 0.65 and average_return >= 0.007:
            regime = "BROAD_RALLY"
            supporting.extend(["breadth.advancing_ratio", "breadth.equal_weight_return"])
        elif down_ratio >= 0.65 and average_return <= -0.007:
            regime = "BROAD_SELL_OFF"
            supporting.extend(["breadth.declining_ratio", "breadth.equal_weight_return"])
        elif index_average is not None and index_average > 0.005 and up_ratio < 0.45:
            regime = "INDEX_LED_RALLY"
            supporting.extend(["indices.average_return", "breadth.advancing_ratio"])
        elif top_concept >= 0.025 and up_ratio < 0.6:
            regime = "THEME_RALLY"
            supporting.extend(["concepts.top_return", "breadth.advancing_ratio"])
        elif average_return < -0.002 and turnover_change <= -0.08:
            regime = "SHRINKING_PULLBACK"
            supporting.extend(["breadth.equal_weight_return", "turnover.change_ratio"])
        elif average_return > 0.001 and (turnover_change < -0.05 or up_ratio < 0.55):
            regime = "WEAK_REBOUND"
            supporting.extend(["breadth.equal_weight_return", "turnover.change_ratio"])
        elif abs(average_return) <= 0.0015 and 0.42 <= up_ratio <= 0.58:
            regime = "RANGE_BOUND"
            supporting.extend(["breadth.equal_weight_return", "breadth.advancing_ratio"])
        else:
            regime = "MIXED_ROTATION"
            supporting.extend(["breadth.equal_weight_return", "style.style_divergence_score"])

        if index_average is None:
            conflicting.append("indices.data_status=NOT_AVAILABLE")
        elif average_return * index_average < 0:
            conflicting.append("indices_vs_breadth_direction")
        confidence = _clamp01(snapshot.data_quality_score / 100 * (0.9 if conflicting else 1.0))
        return MarketRegimeResult(
            market_regime=regime,
            regime_score=round(score, 2),
            regime_confidence=round(confidence, 4),
            supporting_metrics=supporting,
            conflicting_metrics=conflicting,
            data_quality_score=snapshot.data_quality_score,
            version=self.version,
        )


class MarketOutlookRuleEngine:
    def __init__(self, version: str = "market_outlook_rule_v1") -> None:
        self.version = version

    def evaluate(
        self,
        snapshot: MarketDailySnapshotData,
        regime: MarketRegimeResult,
        *,
        external_evidence_score: float = 50.0,
    ) -> MarketOutlookResult:
        breadth = snapshot.breadth
        turnover = snapshot.turnover
        limits = snapshot.limit_structure
        average_return = float(breadth.get("equal_weight_return") or 0)
        up_ratio = float(breadth.get("advancing_ratio") or 0.5)
        turnover_change = float(turnover.get("change_ratio") or 0)
        limit_up = int(limits.get("limit_up_count") or 0)
        limit_down = int(limits.get("limit_down_count") or 0)
        style_divergence = float(snapshot.style.get("style_divergence_score") or 0)

        components = {
            "trend_score": _clamp(50 + average_return * 1600),
            "breadth_score": _clamp(up_ratio * 100),
            "liquidity_score": _clamp(50 + turnover_change * 100),
            "sentiment_score": _clamp(50 + (limit_up - limit_down) * 1.5),
            "sector_diffusion_score": _clamp(70 - style_divergence * 50),
            "volatility_score": _clamp(60 - abs(average_return) * 700),
            "risk_score": _clamp(70 - limit_down * 2 - max(0, -average_return) * 1200),
            "external_evidence_score": _clamp(external_evidence_score),
            "technical_position_score": _clamp(regime.regime_score),
            "data_quality_score": _clamp(snapshot.data_quality_score),
        }
        score = (
            components["trend_score"] * 0.18
            + components["breadth_score"] * 0.18
            + components["liquidity_score"] * 0.11
            + components["sentiment_score"] * 0.12
            + components["sector_diffusion_score"] * 0.08
            + components["volatility_score"] * 0.05
            + components["risk_score"] * 0.12
            + components["external_evidence_score"] * 0.05
            + components["technical_position_score"] * 0.06
            + components["data_quality_score"] * 0.05
        )
        bull = round(20 + max(0.0, score - 50) * 0.45)
        bear = round(20 + max(0.0, 50 - score) * 0.45)
        if snapshot.data_quality_score < 60:
            bull = max(10, bull - 3)
            bear = max(10, bear - 3)
        base = 100 - bull - bear
        scenarios = [
            ScenarioRule(scenario_type="BASE", probability=base, title=_base_title(score, regime.market_regime)),
            ScenarioRule(scenario_type="BULL", probability=bull, title="放量修复与强势扩散"),
            ScenarioRule(scenario_type="BEAR", probability=bear, title="缩量下探或风险扩散"),
        ]
        return MarketOutlookResult(
            market_outlook_score=round(score, 2),
            base_case_probability=base,
            bull_case_probability=bull,
            bear_case_probability=bear,
            probability_model_version=self.version,
            components={key: round(value, 2) for key, value in components.items()},
            scenarios=scenarios,
        )


def _base_title(score: float, regime: str) -> str:
    if regime == "BROAD_RALLY" or score >= 58:
        return "震荡偏强"
    if regime in {"BROAD_SELL_OFF", "SHRINKING_PULLBACK"} or score <= 42:
        return "震荡偏弱"
    if regime == "WEAK_REBOUND":
        return "修复"
    if regime in {"MIXED_ROTATION", "THEME_RALLY", "INDEX_LED_RALLY"}:
        return "延续分化"
    return "震荡"


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
