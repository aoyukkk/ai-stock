from __future__ import annotations

import numpy as np
import pandas as pd

from market_forecast.baselines import baseline_predictions


def test_all_frozen_baselines_use_same_test_rows() -> None:
    train = np.array([1, 1, 0, 1])
    metadata = pd.DataFrame(
        {
            "persistence_prediction": [1, 0, 1],
            "momentum5_prediction": [0, 0, 1],
        }
    )
    values = baseline_predictions(train, metadata)

    assert values["majority"].tolist() == [1, 1, 1]
    assert values["persistence"].tolist() == [1, 0, 1]
    assert values["momentum5"].tolist() == [0, 0, 1]
    assert {len(item) for item in values.values()} == {len(metadata)}
