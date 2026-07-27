from __future__ import annotations

import numpy as np
import pandas as pd


BASELINE_NAMES = ("majority", "persistence", "momentum5")


def baseline_predictions(train_targets: np.ndarray, test_metadata: pd.DataFrame) -> dict[str, np.ndarray]:
    if not len(train_targets):
        raise ValueError("Training targets are required for the majority baseline")
    majority_class = int(float(np.mean(train_targets)) > 0.5)
    return {
        "majority": np.full(len(test_metadata), majority_class, dtype=np.int64),
        "persistence": test_metadata["persistence_prediction"].to_numpy(dtype=np.int64),
        "momentum5": test_metadata["momentum5_prediction"].to_numpy(dtype=np.int64),
    }
