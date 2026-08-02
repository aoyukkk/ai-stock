from __future__ import annotations

import numpy as np

from market_forecast.bootstrap import moving_block_bootstrap
from market_forecast.metrics import classification_metrics


def test_metrics_include_probability_and_confusion_outputs() -> None:
    actual = np.array([0, 0, 1, 1])
    probability = np.array([0.1, 0.6, 0.7, 0.8])
    metrics = classification_metrics(actual, probability)

    assert metrics["sample_count"] == 4
    assert metrics["accuracy"] == 0.75
    assert metrics["confusion_matrix"] == [[1, 1], [0, 2]]
    assert 0 <= metrics["brier_score"] <= 1
    assert metrics["roc_auc"] is not None


def test_moving_block_bootstrap_is_reproducible_and_aligned() -> None:
    model = np.array(([1.0] * 70) + ([0.0] * 30))
    baseline = np.array(([1.0] * 55) + ([0.0] * 45))
    first = moving_block_bootstrap(model, baseline, block_length=10, resamples=200, random_seed=9)
    second = moving_block_bootstrap(model, baseline, block_length=10, resamples=200, random_seed=9)

    assert first == second
    assert first["accuracy_difference"] == 0.15
    assert len(first["improvement_ci_95"]) == 2
