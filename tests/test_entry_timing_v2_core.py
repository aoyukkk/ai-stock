from __future__ import annotations

from types import SimpleNamespace

import pytest

from entry_timing.admission_v2 import StrategyAwareAdmissionEngine, select_v2_pass
from entry_timing.config_v2 import EntryTimingV2ConfigService
from entry_timing.engine import EntryTimingAssessment
from entry_timing.market_emotion import MarketEmotionEngine, StrategyMarketEmotionGate
from entry_timing.strategy import ShortTermStrategyClassifier, StrategyFeatures
from entry_timing.v2 import EntryTimingV2Engine


@pytest.fixture(scope="module")
def config():
    return EntryTimingV2ConfigService().get()


def _features(**overrides):
    values = dict(
        close=10.5, ma5=10.4, ma10=10.2, ma20=10.0, ma60=9.5,
        ma20_slope=.02, ma60_slope=.01, return_1d=.02, return_5d=.10,
        return_10d=.15, distance_20d_high=.02, recent_drawdown=.02,
        volume_ratio=1.5, pullback_volume_ratio=.8, rsi14=60,
        sector_score=80, sector_breadth=70, stock_relative_strength=2,
        sector_rank_percentile=20, market_regime="BROAD_RALLY",
        market_emotion_state="GREEN", reversal_confirmation=True,
        risk_flags=[], data_quality_score=90,
    )
    values.update(overrides)
    return StrategyFeatures(**values)


def test_strategy_classifier_trend_breakout(config) -> None:
    result = ShortTermStrategyClassifier(config).classify(_features(
        sector_score=60, sector_breadth=40, stock_relative_strength=-1, sector_rank_percentile=60,
    ))
    assert result.strategy_id == "TREND_BREAKOUT"
    assert result.classification_status in {"MATCHED", "MULTIPLE_MATCHES"}


def test_strategy_classifier_strong_pullback(config) -> None:
    result = ShortTermStrategyClassifier(config).classify(_features(
        distance_20d_high=.06, recent_drawdown=.06, volume_ratio=.75,
        pullback_volume_ratio=.75, sector_score=55, sector_breadth=40,
        stock_relative_strength=-1, sector_rank_percentile=60,
    ))
    assert result.strategy_id == "STRONG_PULLBACK"


def test_strategy_classifier_sector_resonance(config) -> None:
    result = ShortTermStrategyClassifier(config).classify(_features(
        ma5=9.9, ma10=10.0, volume_ratio=1.0, distance_20d_high=.12,
        sector_score=90, sector_breadth=80, stock_relative_strength=3,
    ))
    assert result.strategy_id == "SECTOR_RESONANCE"


def test_strategy_classifier_oversold_rebound(config) -> None:
    result = ShortTermStrategyClassifier(config).classify(_features(
        close=8.9, ma5=8.8, ma10=9.2, ma20=10, ma60=10.5,
        ma20_slope=-.03, ma60_slope=-.01, return_1d=.03, return_5d=-.12,
        return_10d=-.18, distance_20d_high=.18, recent_drawdown=.18,
        volume_ratio=1.1, rsi14=28, sector_score=40, sector_breadth=35,
        stock_relative_strength=-2, sector_rank_percentile=80,
        market_regime="PANIC_AND_REPAIR", market_emotion_state="RED",
    ))
    assert result.strategy_id == "OVERSOLD_REBOUND"


def test_strategy_classifier_multiple_unclassified_and_missing(config) -> None:
    classifier = ShortTermStrategyClassifier(config)
    multiple = classifier.classify(_features())
    assert multiple.alternative_strategy_ids
    unclassified = classifier.classify(_features(
        ma5=9, ma10=10, ma20=11, ma20_slope=-.1, return_5d=-.02,
        distance_20d_high=.2, recent_drawdown=.2, volume_ratio=1,
        sector_score=30, sector_breadth=30, stock_relative_strength=-3,
        sector_rank_percentile=80, rsi14=50, reversal_confirmation=False,
    ))
    assert unclassified.strategy_id == "UNCLASSIFIED"
    missing = classifier.classify(StrategyFeatures(data_quality_score=20))
    assert missing.classification_status == "DATA_INSUFFICIENT"


def _snapshot(advance=.75, up=90, down=20, failed=.15, median=.015, relative=1.05, below3=300, below5=100):
    return {
        "breadth": {"advancing_ratio": advance, "median_return": median, "valid_count": 5000, "below_3_count": below3, "below_5_count": below5},
        "limit_structure": {"limit_up_count": up, "limit_down_count": down, "failed_limit_up_ratio": failed},
        "turnover": {"relative_to_5d": relative},
    }


def test_market_emotion_rally_mixed_selloff_and_stress(config) -> None:
    engine = MarketEmotionEngine(config)
    assert engine.evaluate(_snapshot()).emotion_state == "GREEN"
    assert engine.evaluate(_snapshot(advance=.48, up=45, down=40, failed=.35, median=-.002, relative=.88, below3=900, below5=400)).emotion_state == "YELLOW"
    selloff = engine.evaluate(_snapshot(advance=.1, up=20, down=200, failed=.55, median=-.04, relative=1.4, below3=3500, below5=2200))
    assert selloff.emotion_state == "RED"
    assert selloff.turnover_health == 15
    missing = engine.evaluate({"breadth": {"advancing_ratio": .7}})
    assert missing.emotion_state == "DATA_INSUFFICIENT"
    assert missing.missing_components


def test_strategy_market_gate_cases(config) -> None:
    gate = StrategyMarketEmotionGate(config)
    assert gate.evaluate("TREND_BREAKOUT", "GREEN", market_regime="BROAD_RALLY", strategy_fit=80, pullback_quality=80, sector_score=80, reversal_confirmation=True)[0] == "PASS"
    assert gate.evaluate("TREND_BREAKOUT", "RED", market_regime="BROAD_SELL_OFF", strategy_fit=80, pullback_quality=80, sector_score=80, reversal_confirmation=True)[0] == "BLOCK"
    assert gate.evaluate("STRONG_PULLBACK", "YELLOW", market_regime="MIXED_ROTATION", strategy_fit=70, pullback_quality=70, sector_score=60, reversal_confirmation=True)[0] == "PASS"
    assert gate.evaluate("OVERSOLD_REBOUND", "RED", market_regime="PANIC_AND_REPAIR", strategy_fit=80, pullback_quality=50, sector_score=40, reversal_confirmation=True)[0] == "REVIEW"
    assert gate.evaluate("OVERSOLD_REBOUND", "GREEN", market_regime="BROAD_RALLY", strategy_fit=80, pullback_quality=50, sector_score=40, reversal_confirmation=True)[0] == "REVIEW"
    assert gate.evaluate("UNCLASSIFIED", "GREEN", market_regime="BROAD_RALLY", strategy_fit=0, pullback_quality=50, sector_score=40, reversal_confirmation=True)[0] == "REVIEW"


def test_entry_timing_v2_weights_missing_renormalization_and_v1_unchanged(config) -> None:
    assert sum(config["weights"].values()) == pytest.approx(1)
    v1 = EntryTimingAssessment(20, 15, 16, 12, 8, 8, 79, 90, "APPROVED", [], {"sector_change": .01})
    before = v1.entry_timing_score
    classification = ShortTermStrategyClassifier(config).classify(_features())
    result = EntryTimingV2Engine(config).evaluate(v1, classification)
    assert 0 <= result.entry_timing_v2_score <= 100
    assert result.component_coverage == pytest.approx(1)
    assert v1.entry_timing_score == before
    missing_sector = EntryTimingAssessment(20, 15, 16, 7.5, 8, 8, 74.5, 70, "WATCH", [], {"sector_change": None})
    missing = EntryTimingV2Engine(config).evaluate(missing_sector, classification)
    assert missing.component_scores["sector_resonance"] is None
    assert missing.component_coverage == pytest.approx(.8)


def test_admission_v2_pass_review_block_and_no_fill(config) -> None:
    engine = StrategyAwareAdmissionEngine(config)
    base = dict(quant_score=80, risk_score=80, timing_score=80, strategy_id="TREND_BREAKOUT", strategy_fit=80, classification_status="MATCHED", market_gate_status="PASS", market_gate_reasons=[], threshold_increment=0, risk_flags=[], data_quality=90)
    assert engine.decide(**base).admission_status == "PASS"
    assert engine.decide(**{**base, "timing_score": 65}).admission_status == "REVIEW"
    assert engine.decide(**{**base, "market_gate_status": "BLOCK", "market_gate_reasons": ["RED"]}).admission_status == "BLOCK"
    assert engine.decide(**{**base, "strategy_id": "UNCLASSIFIED", "strategy_fit": 0}).admission_status == "BLOCK"
    assert engine.decide(**{**base, "strategy_fit": 30}).admission_status == "BLOCK"
    assert select_v2_pass([], 20) == []
    reviews = [SimpleNamespace(admission_status_v2="REVIEW", admission_ranking_score_v2=99, quant_rank=1)]
    assert select_v2_pass(reviews, 20) == []
    passed = [SimpleNamespace(admission_status_v2="PASS", admission_ranking_score_v2=100-index, quant_rank=index) for index in range(25)]
    assert len(select_v2_pass(passed, 20)) == 20


def test_v2_shadow_security_controls_are_all_disabled(config) -> None:
    assert config["enabled"] is False
    assert config["shadow_only"] is True
    safety = config["safety"]
    assert safety["advisory_only"] is True
    for key in ("actionable", "scheduler_enabled", "create_orders", "create_virtual_orders", "real_trading_enabled", "external_calls_enabled", "llm_calls_enabled", "parameter_search_enabled"):
        assert safety[key] is False
