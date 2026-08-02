from __future__ import annotations

from entry_timing.strategy import StrategyFeatures
from backend.api.decision_explainability import router
from database.models.decision_explainability import AdmissionV3Result, FactorPerformanceHistory
from quant.explainability.admission_v3 import AdmissionV3Engine, AdmissionV3Input, calculate_shadow_ev
from quant.explainability.factor_performance import (
    FactorPerformanceCalculator,
    FactorRealizedObservation,
    RealizedReturn,
)
from quant.explainability.gate_evaluation_service import (
    GATE_NAMES,
    GateEvaluationCalculator,
    GateValueObservation,
)
from quant.explainability.strategy_probability import StrategyProbabilityClassifier


def test_factor_performance_calculates_realized_family_metrics_without_reweighting() -> None:
    outcome_win = RealizedReturn(0.02, 0.06, 0.10, 0.10, -0.03)
    outcome_loss = RealizedReturn(-0.01, -0.03, -0.05, -0.05, -0.08)
    results = FactorPerformanceCalculator().aggregate([
        FactorRealizedObservation("MOMENTUM", 5.2, outcome_win),
        FactorRealizedObservation("MOMENTUM", 3.0, outcome_loss),
        FactorRealizedObservation("FUNDAMENTAL", 0.0, outcome_win),
    ], period="2026-07-13..2026-07-20")
    by_family = {row.factor_family: row for row in results}
    momentum = by_family["MOMENTUM"]
    assert momentum.sample_count == 2
    assert momentum.win_rate == 0.5
    assert momentum.avg_return_d1 == 0.005
    assert momentum.avg_return_d5 == 0.025
    assert momentum.avg_drawdown == -0.055
    assert momentum.positive_contribution_rate == 0.5
    assert momentum.negative_contribution_rate == 0.5
    assert by_family["FUNDAMENTAL"].sample_count == 0


def test_gate_evaluation_calculates_value_and_false_positive_rate() -> None:
    observations = [
        GateValueObservation("High Position Risk", True, True, RealizedReturn(-0.1, None, None, -0.1, -0.12)),
        GateValueObservation("High Position Risk", True, True, RealizedReturn(0.2, None, None, 0.2, -0.02)),
        GateValueObservation("Market Emotion Gate", True, False, RealizedReturn(0.05, None, None, 0.05, -0.01)),
    ]
    results = GateEvaluationCalculator().aggregate(observations)
    assert {row.gate_name for row in results} == set(GATE_NAMES)
    high_position = next(row for row in results if row.gate_name == "High Position Risk")
    assert high_position.trigger_count == 2
    assert high_position.blocked_count == 2
    assert high_position.avoided_loss == 0.1
    assert high_position.missed_gain == 0.2
    assert high_position.net_gate_value == -0.1
    assert high_position.false_positive_rate == 0.5
    market = next(row for row in results if row.gate_name == "Market Emotion Gate")
    assert market.future_return == 0.05
    assert market.blocked_count == 0


def test_strategy_probability_uses_required_open_set_key_and_sums_to_one() -> None:
    classifier = StrategyProbabilityClassifier()
    result = classifier.classify(StrategyFeatures(data_quality_score=10))
    assert set(result.strategy_probability) == {
        "TREND_BREAKOUT",
        "STRONG_PULLBACK",
        "SECTOR_RESONANCE",
        "OVERSOLD_REBOUND",
        "OPEN_SET",
    }
    assert sum(result.strategy_probability.values()) == 1.0
    assert result.strategy_probability["OPEN_SET"] == 0.7
    assert result.classification_status == "OPEN_SET"


def test_ev_fields_are_additional_shadow_diagnostics_only() -> None:
    value = AdmissionV3Input(
        quant_score=80,
        entry_timing_score=75,
        sector_strength=70,
        momentum_score=85,
        strategy_probability={
            "TREND_BREAKOUT": 0.45,
            "STRONG_PULLBACK": 0.25,
            "SECTOR_RESONANCE": 0.15,
            "OVERSOLD_REBOUND": 0.05,
            "OPEN_SET": 0.10,
        },
        strategy_status="PROBABILISTIC",
        market_emotion_state="RED",
    )
    decision = AdmissionV3Engine().decide(value)
    original = (decision.admission_state, decision.final_score)
    ev = calculate_shadow_ev(value, decision)
    assert 0 <= ev.expected_value_score <= 100
    assert 0 <= ev.risk_adjusted_opportunity_score <= ev.expected_value_score
    assert (decision.admission_state, decision.final_score) == original


def test_enhancement_schema_and_read_only_routes_are_exposed() -> None:
    assert FactorPerformanceHistory.__tablename__ == "factor_performance_history"
    assert "expected_value_score" in AdmissionV3Result.__table__.columns
    assert "risk_adjusted_opportunity_score" in AdmissionV3Result.__table__.columns
    paths = {route.path for route in router.routes}
    assert "/api/workbench/decision-explainability/factor-performance/latest" in paths
    assert "/api/workbench/decision-explainability/gate-ranking" in paths
    assert all("POST" not in (route.methods or set()) for route in router.routes)
