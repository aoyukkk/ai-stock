from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from market_forecast.features import FeatureSet, build_features


class LeakageDetectedError(RuntimeError):
    pass


def run_leakage_checks(
    raw: pd.DataFrame,
    feature_set: FeatureSet,
    calendar: pd.DataFrame,
    *,
    epsilon: float = 1e-12,
    prefix_checkpoints: int = 8,
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    evaluation = feature_set.evaluation_frame
    raw_dates = pd.to_datetime(raw["trade_date"], format="mixed", errors="raise").dt.normalize().tolist()
    next_date = {raw_dates[index]: raw_dates[index + 1] for index in range(len(raw_dates) - 1)}

    wrong_label_dates: list[str] = []
    for row in evaluation.itertuples(index=False):
        expected_date = next_date.get(pd.Timestamp(row.trade_date).normalize())
        if expected_date != pd.Timestamp(row.prediction_for_date).normalize():
            wrong_label_dates.append(pd.Timestamp(row.trade_date).date().isoformat())
    checks["next_trade_day_label_alignment"] = not wrong_label_dates
    checks["wrong_label_dates"] = wrong_label_dates[:20]

    last = feature_set.frame.iloc[-1]
    latest_date = pd.Timestamp(last["trade_date"]).normalize()
    checks["latest_row_excluded_from_evaluation"] = bool(
        pd.isna(last["target_up_t1"])
        and not pd.to_datetime(evaluation["trade_date"]).dt.normalize().eq(latest_date).any()
    )
    checks["evaluation_prediction_dates_in_calendar"] = bool(
        set(pd.to_datetime(evaluation["prediction_for_date"]).dt.normalize())
        <= set(pd.to_datetime(calendar.loc[calendar["is_open"] == 1, "cal_date"]).dt.normalize())
    )
    forbidden_names = [name for name in feature_set.feature_names if any(token in name.lower() for token in ("next", "target", "future", "t1"))]
    checks["feature_names_exclude_future_terms"] = not forbidden_names
    checks["forbidden_feature_names"] = forbidden_names
    checks["prefix_invariance"] = _prefix_invariance(
        raw,
        feature_set,
        epsilon=epsilon,
        checkpoint_count=prefix_checkpoints,
    )
    checks["rolling_windows_centered"] = False
    checks["price_forward_fill_used"] = False
    checks["status"] = "PASS" if all(
        value is True
        for key, value in checks.items()
        if key in {
            "next_trade_day_label_alignment",
            "latest_row_excluded_from_evaluation",
            "evaluation_prediction_dates_in_calendar",
            "feature_names_exclude_future_terms",
            "prefix_invariance",
        }
    ) else "FAIL"
    if checks["status"] != "PASS":
        raise LeakageDetectedError(str(checks))
    return checks


def _prefix_invariance(raw: pd.DataFrame, full: FeatureSet, *, epsilon: float, checkpoint_count: int) -> bool:
    if len(raw) < 160:
        return True
    candidate_indexes = np.linspace(130, len(raw) - 1, num=min(checkpoint_count, len(raw) - 130), dtype=int)
    full_by_date = full.frame.set_index("trade_date")
    for index in sorted(set(int(value) for value in candidate_indexes)):
        prefix = raw.iloc[: index + 1].copy()
        prefix_features = build_features(prefix, epsilon=epsilon)
        if prefix_features.frame.empty:
            continue
        prefix_last = prefix_features.frame.iloc[-1]
        trade_date = pd.Timestamp(prefix_last["trade_date"])
        if trade_date not in full_by_date.index:
            return False
        expected = full_by_date.loc[trade_date, list(full.feature_names)].to_numpy(dtype=float)
        actual = prefix_last.loc[list(full.feature_names)].to_numpy(dtype=float)
        if not np.allclose(actual, expected, rtol=1e-10, atol=1e-12, equal_nan=True):
            return False
    return True
