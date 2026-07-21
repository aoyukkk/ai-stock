from __future__ import annotations

import json
from datetime import date

import pytest

from entry_timing.historical_v2 import HistoricalEntryTimingV2Validator, _all_metrics


def _daily(path, trade_date: str, pct_chg: float) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{trade_date}.json").write_text(json.dumps([{
        "ts_code": "000001.SZ", "pct_chg": pct_chg, "pre_close": 10,
        "high": 10.5, "low": 9.5,
    }]), encoding="utf-8")


def test_historical_outcome_never_reads_selection_day_or_future_cutoff(tmp_path) -> None:
    daily = tmp_path / "trade_date" / "daily"
    _daily(daily, "20260713", 99)
    _daily(daily, "20260714", 1)
    _daily(daily, "20260715", 2)
    _daily(daily, "20260716", 99)
    validator = HistoricalEntryTimingV2Validator.__new__(HistoricalEntryTimingV2Validator)
    validator.cache_root = tmp_path
    result = validator._outcome(date(2026, 7, 13), "000001.SZ", date(2026, 7, 15))
    assert result["holding_days"] == 2
    assert result["d1_return"] == pytest.approx(0.01)
    assert result["d3_return"] is None
    assert result["cumulative_return"] == pytest.approx(0.0302)


def test_historical_metrics_keep_missing_horizons_missing_and_use_worst_drawdown() -> None:
    rows = [
        {"cumulative_return": .1, "d1_return": .02, "d3_return": None, "d5_return": None, "mae": -.03, "mfe": .12, "max_drawdown": -.04},
        {"cumulative_return": -.2, "d1_return": None, "d3_return": None, "d5_return": None, "mae": -.25, "mfe": .01, "max_drawdown": -.22},
    ]
    metrics = _all_metrics(rows)
    assert metrics["d1"]["observed_count"] == 1
    assert metrics["d3"]["observed_count"] == 0
    assert metrics["d5"]["average_return"] is None
    assert metrics["max_drawdown"] == -.22
    assert metrics["tail_losses"][-.1] == 1
