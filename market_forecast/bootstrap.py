from __future__ import annotations

from typing import Any

import numpy as np


def moving_block_bootstrap(
    model_correct: np.ndarray,
    baseline_correct: np.ndarray,
    *,
    block_length: int = 20,
    resamples: int = 5000,
    random_seed: int = 20260721,
) -> dict[str, Any]:
    model = np.asarray(model_correct, dtype=float)
    baseline = np.asarray(baseline_correct, dtype=float)
    if model.shape != baseline.shape or model.ndim != 1 or len(model) < block_length:
        raise ValueError("Bootstrap inputs must be aligned one-dimensional series longer than one block")
    rng = np.random.default_rng(random_seed)
    differences = np.empty(resamples, dtype=float)
    accuracies = np.empty(resamples, dtype=float)
    maximum_start = len(model) - block_length
    for index in range(resamples):
        blocks: list[np.ndarray] = []
        collected = 0
        while collected < len(model):
            start = int(rng.integers(0, maximum_start + 1))
            block = np.arange(start, start + block_length)
            blocks.append(block)
            collected += len(block)
        sample_indexes = np.concatenate(blocks)[: len(model)]
        sampled_model = model[sample_indexes]
        sampled_baseline = baseline[sample_indexes]
        accuracies[index] = float(sampled_model.mean())
        differences[index] = float((sampled_model - sampled_baseline).mean())
    point_difference = float((model - baseline).mean())
    return {
        "method": "moving_block_bootstrap",
        "sample_count": int(len(model)),
        "block_length": int(block_length),
        "resamples": int(resamples),
        "random_seed": int(random_seed),
        "accuracy_difference": point_difference,
        "improvement_ci_95": [float(value) for value in np.quantile(differences, [0.025, 0.975])],
        "one_sided_p_value": float((1 + np.count_nonzero(differences <= 0.0)) / (resamples + 1)),
        "gru_accuracy": float(model.mean()),
        "gru_accuracy_ci_95": [float(value) for value in np.quantile(accuracies, [0.025, 0.975])],
    }
