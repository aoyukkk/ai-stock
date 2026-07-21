from __future__ import annotations

from post_close.scoring import IFindDataQuality, IFindEodEnhancementEngine, IFindEodFeatures, ScoringProfile


CONFIG = {
    "component_weights": {"relative_strength": .25, "close_quality": .20, "tail_strength": .20, "intraday_stability": .15, "liquidity_confirmation": .10, "market_regime_fit": .10},
    "overlay": {"scale": .12, "minimum": -6, "maximum": 6, "snapshot_only_minimum": -2, "snapshot_only_maximum": 2},
}


def features() -> IFindEodFeatures:
    return IFindEodFeatures(.05, .01, .9, .02, .01, .02, .03, .2, .02, .8, .01, 1.0)


def quality() -> IFindDataQuality:
    return IFindDataQuality(1, 1, 1, 1, 1)


def test_ifind_unavailable_preserves_tushare_baseline_exactly():
    result = IFindEodEnhancementEngine(CONFIG).score(73.125, None, None)
    assert result.scoring_profile == ScoringProfile.TUSHARE_BASELINE_V1
    assert result.ifind_eod_score is None
    assert result.overlay_delta == 0
    assert result.enhanced_score == 73.125
    assert result.fallback_reason == "IFIND_UNAVAILABLE"


def test_overlay_is_bounded_and_snapshot_only_is_more_conservative():
    engine = IFindEodEnhancementEngine(CONFIG)
    full = engine.score(50, features(), quality())
    snapshot = engine.score(50, features(), quality(), minute_available=False)
    assert -6 <= full.overlay_delta <= 6
    assert -2 <= snapshot.overlay_delta <= 2


def test_material_conflict_disables_overlay_without_zeroing_stock_score():
    result = IFindEodEnhancementEngine(CONFIG).score(81, features(), quality(), dual_source_status="MATERIAL_CONFLICT")
    assert result.overlay_delta == 0
    assert result.enhanced_score == 81
    assert result.scoring_profile == ScoringProfile.IFIND_SHADOW_V1


def test_data_quality_coefficient_is_multiplicative():
    value = IFindDataQuality(1, .5, .8, 1, .5)
    assert value.coefficient == .2
