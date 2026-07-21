from __future__ import annotations

from backend.core.config import get_app_config
from entry_timing.config_v2 import EntryTimingV2ConfigService
from entry_timing.strategy import ShortTermStrategyClassifier, StrategyFeatures
from midday.strategy_validation import MiddayStrategyValidator


def _validator():
    config = EntryTimingV2ConfigService(get_app_config()).get()
    return MiddayStrategyValidator(ShortTermStrategyClassifier(config))


def test_historical_strategy_is_not_replaced_by_half_day_unclassified():
    result, live = _validator().evaluate("SECTOR_RESONANCE", 76, StrategyFeatures(close=10, data_quality_score=100))
    assert live.strategy_id == "UNCLASSIFIED"
    assert result.strategy_id == "SECTOR_RESONANCE"
    assert result.strategy_source == "HISTORICAL_PRIMARY"
    assert result.live_strategy_status in {"WEAKENED", "INVALIDATED"}


def test_valid_historical_strategy_can_be_confirmed():
    features = StrategyFeatures(
        close=10, sector_score=90, sector_breadth=.8, stock_relative_strength=2,
        market_emotion_state="GREEN", data_quality_score=100,
    )
    result, _ = _validator().evaluate("SECTOR_RESONANCE", 70, features)
    assert result.strategy_id == "SECTOR_RESONANCE"
    assert result.live_strategy_status == "CONFIRMED"
    assert result.strategy_still_valid
    assert result.live_strategy_fit_delta is not None


def test_no_historical_strategy_uses_live_fallback_but_keeps_unclassified():
    result, _ = _validator().evaluate("UNCLASSIFIED", 0, StrategyFeatures(close=10, data_quality_score=100))
    assert result.strategy_source == "LIVE_FALLBACK"
    assert result.strategy_id == "UNCLASSIFIED"
    assert not result.strategy_still_valid


def test_insufficient_live_data_does_not_erase_historical_strategy():
    result, _ = _validator().evaluate("TREND_BREAKOUT", 80, StrategyFeatures(data_quality_score=0))
    assert result.strategy_id == "TREND_BREAKOUT"
    assert result.live_strategy_status == "DATA_INSUFFICIENT"
    assert result.live_strategy_fit_delta is None
