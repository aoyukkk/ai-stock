from __future__ import annotations

import numpy as np

from market_forecast.features import build_features
from tests.market_forecast_test_utils import synthetic_index_frame


def test_feature_set_has_frozen_40_features_and_next_session_label() -> None:
    raw = synthetic_index_frame(periods=400)
    result = build_features(raw)
    row = result.evaluation_frame.iloc[20]
    raw_index = raw.index[raw["trade_date"] == row["trade_date"]][0]
    expected_return = raw.iloc[raw_index + 1]["close"] / raw.iloc[raw_index]["close"] - 1.0

    assert len(result.feature_names) == 40
    assert np.isfinite(result.frame[list(result.feature_names)].to_numpy()).all()
    assert row["prediction_for_date"] == raw.iloc[raw_index + 1]["trade_date"]
    assert row["next_return_t1"] == expected_return
    assert row["target_up_t1"] == int(expected_return > 0)
    assert result.frame.iloc[-1]["target_up_t1"] != result.frame.iloc[-1]["target_up_t1"]


def test_future_rows_do_not_change_existing_features() -> None:
    raw = synthetic_index_frame(periods=450)
    full = build_features(raw)
    prefix = build_features(raw.iloc[:350])
    date = prefix.frame.iloc[-1]["trade_date"]
    expected = full.frame.set_index("trade_date").loc[date, list(full.feature_names)].to_numpy(dtype=float)
    actual = prefix.frame.iloc[-1][list(prefix.feature_names)].to_numpy(dtype=float)

    assert np.allclose(actual, expected, rtol=1e-10, atol=1e-12)
