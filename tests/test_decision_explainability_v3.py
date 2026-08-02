from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.api.decision_explainability import router
from entry_timing.strategy import StrategyFeatures
from quant.explainability.admission_v3 import AdmissionV3Engine, AdmissionV3Input
from quant.explainability.factor_attribution import FactorAttributionEngine
from quant.explainability.factor_registry import (
    FACTOR_FAMILIES,
    RISK_LIQUIDITY,
    SENTIMENT_REGIME,
)
from quant.explainability.gate_evaluation import GateCounterfactualEvaluator, GateObservation
from quant.explainability.llm_shadow import LLMShadowStructuredOutput, calculate_local_llm_score
from quant.explainability.service import DecisionExplainabilityShadowService, SHADOW_CONFIG
from quant.explainability.strategy_probability import StrategyProbabilityClassifier
from quant.explainability.timing_contract import (
    INVALID_TIMING_CONTRACT,
    StrategyTimingContractSpec,
    compute_forward_return,
    validate_timing_contract,
)


TRADE_DATE = date(2026, 7, 20)


def _contract(**overrides) -> StrategyTimingContractSpec:
    observation = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)
    values = {
        "stock_code": "600126.SH",
        "trade_date": TRADE_DATE,
        "observation_end_ts": observation,
        "available_at_ts": observation + timedelta(minutes=5),
        "signal_generated_at": observation + timedelta(minutes=10),
        "order_eligible_at": observation + timedelta(hours=18),
        "execution_policy": "NEXT_TRADING_DAY_OPEN_SHADOW",
        "feature_version": "test-v1",
        "data_snapshot_id": "manifest-test",
        "universe_snapshot_id": "universe-test",
    }
    values.update(overrides)
    return StrategyTimingContractSpec(**values)


def _features(**overrides) -> StrategyFeatures:
    values = {
        "close": 10.5,
        "ma5": 10.4,
        "ma10": 10.2,
        "ma20": 10.0,
        "ma60": 9.5,
        "ma20_slope": 0.02,
        "ma60_slope": 0.01,
        "return_1d": 0.02,
        "return_5d": 0.10,
        "return_10d": 0.15,
        "distance_20d_high": 0.02,
        "recent_drawdown": 0.02,
        "volume_ratio": 1.5,
        "pullback_volume_ratio": 0.8,
        "rsi14": 60.0,
        "sector_score": 80.0,
        "sector_breadth": 70.0,
        "stock_relative_strength": 2.0,
        "sector_rank_percentile": 20.0,
        "market_regime": "RISK_ON",
        "market_emotion_state": "GREEN",
        "reversal_confirmation": True,
        "risk_flags": [],
        "data_quality_score": 90.0,
    }
    values.update(overrides)
    return StrategyFeatures(**values)


def test_timing_contract_blocks_leakage_before_return_calculation() -> None:
    valid = _contract()
    validate_timing_contract(valid)
    called = False

    def calculator() -> float:
        nonlocal called
        called = True
        return 0.1

    invalid = _contract(signal_generated_at=valid.order_eligible_at)
    with pytest.raises(ValueError, match=INVALID_TIMING_CONTRACT):
        compute_forward_return(invalid, calculator)
    assert called is False


def test_factor_attribution_has_six_families_lineage_and_frozen_weights() -> None:
    rows = FactorAttributionEngine().attribute(
        stock_code="600126.SH",
        trade_date=TRADE_DATE,
        technical_score=80,
        capital_score=70,
        emotion_score=60,
        momentum_score=90,
        risk_score=50,
        flash_score=75,
        raw_metrics={"return_5d": 0.08, "volume_ratio": 1.4},
        gate_contributions={SENTIMENT_REGIME: -10, RISK_LIQUIDITY: -3.8},
    )
    assert {row.factor_family for row in rows} == set(FACTOR_FAMILIES)
    by_family = {row.factor_family: row for row in rows}
    assert by_family["POSITION_TREND"].score_contribution == pytest.approx(20.0)
    assert by_family[SENTIMENT_REGIME].gate_contribution == pytest.approx(-10.0)
    assert by_family[RISK_LIQUIDITY].rank_contribution == pytest.approx(3.7)
    assert by_family["MOMENTUM"].lineage[0]["decision_path"][-2:] == ["ADMISSION_V3_SHADOW", "FINAL_EXPLANATION"]
    assert by_family["FUNDAMENTAL"].score_contribution == 0


def test_gate_counterfactual_is_penalty_not_direct_block_and_has_value_metrics() -> None:
    probabilities = StrategyProbabilityClassifier().classify(_features()).strategy_probability
    value = AdmissionV3Input(
        quant_score=90,
        entry_timing_score=90,
        sector_strength=90,
        momentum_score=90,
        strategy_probability=probabilities,
        strategy_status="PROBABILISTIC",
        market_emotion_state="RED",
        risk_flags=("HIGH_POSITION_RISK",),
    )
    decision = AdmissionV3Engine().decide(value)
    assert decision.admission_state != "REJECT"
    assert decision.risk_penalties["MARKET_RED"]["score_penalty"] == 10
    assert decision.counterfactuals["MARKET_RED"]["final_score"] > decision.final_score

    observations = [
        GateObservation(_contract(stock_code="600126.SH"), True, lambda: -0.1),
        GateObservation(_contract(stock_code="688519.SH"), True, lambda: 0.2),
        GateObservation(_contract(stock_code="000001.SZ"), False, lambda: 1.0),
    ]
    result = GateCounterfactualEvaluator().evaluate("HIGH_POSITION_RISK", observations)
    assert result.blocked_count == 2
    assert result.avoided_loss == pytest.approx(0.1)
    assert result.missed_gain == pytest.approx(0.2)
    assert result.net_gate_value == pytest.approx(-0.1)


def test_strategy_probability_sums_to_one_and_open_set_does_not_block() -> None:
    classifier = StrategyProbabilityClassifier()
    classified = classifier.classify(_features())
    assert sum(classified.strategy_probability.values()) == pytest.approx(1.0)
    assert classified.classification_status == "PROBABILISTIC"
    open_set = classifier.classify(StrategyFeatures(data_quality_score=10))
    assert open_set.classification_status == "OPEN_SET"
    assert open_set.primary_strategy == "OPEN_SET"
    decision = AdmissionV3Engine().decide(AdmissionV3Input(
        quant_score=20,
        entry_timing_score=20,
        sector_strength=20,
        momentum_score=20,
        strategy_probability=open_set.strategy_probability,
        strategy_status=open_set.classification_status,
    ))
    assert decision.admission_state == "REVIEW"


def test_hard_safety_gate_rejects_and_llm_score_is_local() -> None:
    probability = StrategyProbabilityClassifier().classify(_features()).strategy_probability
    decision = AdmissionV3Engine().decide(AdmissionV3Input(
        quant_score=95,
        entry_timing_score=95,
        sector_strength=95,
        momentum_score=95,
        strategy_probability=probability,
        strategy_status="PROBABILISTIC",
        future_data_detected=True,
    ))
    assert decision.admission_state == "REJECT"
    assert decision.rejected_reasons == ["FUTURE_DATA"]
    llm_score = calculate_local_llm_score(LLMShadowStructuredOutput(80, 70, 60, 90, 50))
    assert llm_score.final_llm_score == pytest.approx(70.5)


def test_shadow_contract_keeps_baseline_and_exposes_read_only_page() -> None:
    DecisionExplainabilityShadowService._assert_frozen_quant_baseline()
    assert SHADOW_CONFIG["baseline"] == "TUSHARE_BASELINE_V1"
    assert list(SHADOW_CONFIG["quant_weights"].values()) == [0.25, 0.25, 0.20, 0.15, 0.15]
    assert SHADOW_CONFIG["production_enabled"] is False
    paths_and_methods = {(route.path, tuple(route.methods or ())) for route in router.routes}
    assert all("POST" not in methods for _, methods in paths_and_methods)
    assert any(path.endswith("/latest") for path, _ in paths_and_methods)
    root = Path(__file__).resolve().parents[1]
    view = (root / "frontend/src/views/DecisionExplainabilityView.vue").read_text(encoding="utf-8")
    for text in ("为什么选中", "为什么拒绝", "最大贡献因子", "最大影响门禁", "取消门禁后的变化"):
        assert text in view
