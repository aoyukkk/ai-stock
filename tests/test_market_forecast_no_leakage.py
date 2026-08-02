from __future__ import annotations

from pathlib import Path

from market_forecast.features import build_features
from market_forecast.leakage_checks import run_leakage_checks
from tests.market_forecast_test_utils import synthetic_calendar, synthetic_index_frame


def test_automatic_leakage_checks_pass_for_causal_features() -> None:
    raw = synthetic_index_frame(periods=500)
    result = build_features(raw)
    report = run_leakage_checks(raw, result, synthetic_calendar(raw), prefix_checkpoints=4)

    assert report["status"] == "PASS"
    assert report["next_trade_day_label_alignment"] is True
    assert report["latest_row_excluded_from_evaluation"] is True
    assert report["prefix_invariance"] is True


def test_feature_implementation_does_not_use_centered_rolling_windows() -> None:
    source = (Path(__file__).resolve().parents[1] / "market_forecast" / "features.py").read_text(encoding="utf-8")

    assert "center=True" not in source.replace(" ", "")
