from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class SequenceCollection:
    values: np.ndarray
    targets: np.ndarray
    metadata: pd.DataFrame
    feature_names: tuple[str, ...]
    lookback_days: int

    def take(self, indexes: np.ndarray) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
        return self.values[indexes], self.targets[indexes], self.metadata.iloc[indexes].reset_index(drop=True)


def build_sequences(
    feature_frame: pd.DataFrame,
    feature_names: tuple[str, ...] | list[str],
    *,
    lookback_days: int,
) -> SequenceCollection:
    names = tuple(feature_names)
    ordered = feature_frame.sort_values("trade_date", kind="stable").reset_index(drop=True)
    matrix = ordered.loc[:, names].to_numpy(dtype=np.float32)
    sequences: list[np.ndarray] = []
    targets: list[int] = []
    metadata_rows: list[dict[str, Any]] = []
    for endpoint in range(lookback_days - 1, len(ordered)):
        row = ordered.iloc[endpoint]
        if pd.isna(row["target_up_t1"]) or pd.isna(row["prediction_for_date"]):
            continue
        window = matrix[endpoint - lookback_days + 1 : endpoint + 1]
        if window.shape != (lookback_days, len(names)) or not np.isfinite(window).all():
            continue
        sequences.append(window.copy())
        targets.append(int(row["target_up_t1"]))
        metadata_rows.append(
            {
                "trade_date": pd.Timestamp(row["trade_date"]).normalize(),
                "prediction_for_date": pd.Timestamp(row["prediction_for_date"]).normalize(),
                "actual_return": float(row["next_return_t1"]),
                "actual_direction": int(row["target_up_t1"]),
                "persistence_prediction": int(float(row["close_return_1d"]) > 0),
                "momentum5_prediction": int(float(row["cumulative_return_5d"]) > 0),
                "sequence_start_date": pd.Timestamp(ordered.iloc[endpoint - lookback_days + 1]["trade_date"]).normalize(),
                "sequence_end_date": pd.Timestamp(row["trade_date"]).normalize(),
            }
        )
    if not sequences:
        raise ValueError("No valid labeled sequences were generated")
    return SequenceCollection(
        values=np.stack(sequences).astype(np.float32),
        targets=np.asarray(targets, dtype=np.int64),
        metadata=pd.DataFrame(metadata_rows),
        feature_names=names,
        lookback_days=lookback_days,
    )


def fit_training_scaler(values: np.ndarray) -> StandardScaler:
    if values.ndim != 3:
        raise ValueError("Sequence tensor must have shape samples x lookback x features")
    scaler = StandardScaler()
    scaler.fit(values.reshape(-1, values.shape[-1]))
    return scaler


def transform_sequences(values: np.ndarray, scaler: StandardScaler) -> np.ndarray:
    shape = values.shape
    transformed = scaler.transform(values.reshape(-1, shape[-1])).reshape(shape)
    return transformed.astype(np.float32)
