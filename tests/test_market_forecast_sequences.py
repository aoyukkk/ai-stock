from __future__ import annotations

import numpy as np

from market_forecast.features import build_features
from market_forecast.sequence_dataset import build_sequences, fit_training_scaler, transform_sequences
from tests.market_forecast_test_utils import synthetic_index_frame


def test_sequence_window_ends_at_prediction_time_t() -> None:
    result = build_features(synthetic_index_frame(periods=500))
    sequences = build_sequences(result.frame, result.feature_names, lookback_days=60)

    assert sequences.values.shape[1:] == (60, 40)
    assert (sequences.metadata["sequence_end_date"] == sequences.metadata["trade_date"]).all()
    assert (sequences.metadata["prediction_for_date"] > sequences.metadata["trade_date"]).all()
    assert (sequences.metadata["sequence_start_date"] < sequences.metadata["sequence_end_date"]).all()


def test_scaler_is_fitted_only_on_training_tensor() -> None:
    train = np.zeros((10, 4, 2), dtype=np.float32)
    validation = np.full((3, 4, 2), 1000.0, dtype=np.float32)
    scaler = fit_training_scaler(train)
    transformed = transform_sequences(validation, scaler)

    assert np.allclose(scaler.mean_, [0.0, 0.0])
    assert (transformed > 100.0).all()
