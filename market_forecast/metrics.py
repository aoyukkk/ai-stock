from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


def classification_metrics(
    actual: np.ndarray,
    probabilities: np.ndarray,
    *,
    threshold: float = 0.50,
) -> dict[str, Any]:
    y_true = np.asarray(actual, dtype=np.int64)
    y_prob = np.clip(np.asarray(probabilities, dtype=float), 1e-7, 1 - 1e-7)
    y_pred = (y_prob >= threshold).astype(np.int64)
    matrix = confusion_matrix(y_true, y_pred, labels=[0, 1])
    roc_auc = float(roc_auc_score(y_true, y_prob)) if len(np.unique(y_true)) == 2 else None
    return {
        "sample_count": int(len(y_true)),
        "up_ratio": float(y_true.mean()) if len(y_true) else None,
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": roc_auc,
        "brier_score": float(brier_score_loss(y_true, y_prob)),
        "log_loss": float(log_loss(y_true, y_prob, labels=[0, 1])),
        "confusion_matrix": matrix.astype(int).tolist(),
        "predicted_up_ratio": float(y_pred.mean()) if len(y_pred) else None,
        "mean_predicted_probability": float(y_prob.mean()) if len(y_prob) else None,
    }


def hard_prediction_metrics(actual: np.ndarray, prediction: np.ndarray) -> dict[str, Any]:
    return classification_metrics(actual, np.asarray(prediction, dtype=float), threshold=0.50)


def calibration_table(actual: np.ndarray, probabilities: np.ndarray, *, bin_count: int = 10) -> list[dict[str, Any]]:
    frame = pd.DataFrame({"actual": np.asarray(actual, dtype=int), "probability": np.asarray(probabilities, dtype=float)})
    edges = np.linspace(0.0, 1.0, bin_count + 1)
    frame["bin"] = pd.cut(frame["probability"], bins=edges, include_lowest=True, right=True, duplicates="drop")
    rows: list[dict[str, Any]] = []
    for interval, group in frame.groupby("bin", observed=False):
        if group.empty:
            continue
        rows.append(
            {
                "probability_bin": str(interval),
                "sample_count": int(len(group)),
                "mean_predicted_probability": float(group["probability"].mean()),
                "actual_up_rate": float(group["actual"].mean()),
            }
        )
    return rows


def metrics_record(prefix: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    output = dict(prefix)
    for key, value in metrics.items():
        output[key] = json.dumps(value, ensure_ascii=False) if key == "confusion_matrix" else value
    return output
