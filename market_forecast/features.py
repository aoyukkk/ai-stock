from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


RETURN_WINDOWS = (2, 3, 5, 10, 20, 60)
VOLATILITY_WINDOWS = (5, 10, 20, 60)
MA_WINDOWS = (5, 10, 20, 60)


@dataclass(frozen=True)
class FeatureSet:
    frame: pd.DataFrame
    evaluation_frame: pd.DataFrame
    feature_names: tuple[str, ...]
    dictionary: list[dict[str, Any]]


def build_features(frame: pd.DataFrame, *, epsilon: float = 1e-12) -> FeatureSet:
    data = frame.copy().sort_values("trade_date", kind="stable").reset_index(drop=True)
    data["trade_date"] = pd.to_datetime(data["trade_date"], format="mixed", errors="raise").dt.normalize()
    for column in ("open", "high", "low", "close", "pre_close", "vol", "amount"):
        data[column] = pd.to_numeric(data[column], errors="coerce")

    features: dict[str, pd.Series] = {}
    features["close_return_1d"] = _safe_ratio(data["close"], data["pre_close"], epsilon) - 1.0
    features["open_gap"] = _safe_ratio(data["open"], data["pre_close"], epsilon) - 1.0
    features["intraday_return"] = _safe_ratio(data["close"], data["open"], epsilon) - 1.0
    features["high_low_range"] = _safe_ratio(data["high"] - data["low"], data["pre_close"], epsilon)
    features["upper_shadow"] = _safe_ratio(data["high"] - data[["open", "close"]].max(axis=1), data["pre_close"], epsilon)
    features["lower_shadow"] = _safe_ratio(data[["open", "close"]].min(axis=1) - data["low"], data["pre_close"], epsilon)
    features["close_position"] = _safe_ratio(data["close"] - data["low"], data["high"] - data["low"], epsilon)
    features["log_volume"] = np.log1p(data["vol"])
    features["log_amount"] = np.log1p(data["amount"])
    features["volume_change_1d"] = data["vol"].pct_change(fill_method=None)
    features["amount_change_1d"] = data["amount"].pct_change(fill_method=None)

    returns = features["close_return_1d"]
    for window in RETURN_WINDOWS:
        features[f"cumulative_return_{window}d"] = data["close"].pct_change(periods=window, fill_method=None)
    for window in VOLATILITY_WINDOWS:
        features[f"return_volatility_{window}d"] = returns.rolling(window=window, min_periods=window).std(ddof=0)
    for window in MA_WINDOWS:
        moving_average = data["close"].rolling(window=window, min_periods=window).mean()
        features[f"close_to_ma_{window}d"] = _safe_ratio(data["close"], moving_average, epsilon) - 1.0

    for window in (5, 20):
        volume_average = data["vol"].rolling(window=window, min_periods=window).mean()
        amount_average = data["amount"].rolling(window=window, min_periods=window).mean()
        features[f"volume_to_ma_{window}d"] = _safe_ratio(data["vol"], volume_average, epsilon)
        features[f"amount_to_ma_{window}d"] = _safe_ratio(data["amount"], amount_average, epsilon)

    delta = data["close"].diff()
    gains = delta.clip(lower=0).rolling(window=14, min_periods=14).mean()
    losses = (-delta.clip(upper=0)).rolling(window=14, min_periods=14).mean()
    relative_strength = _safe_ratio(gains, losses, epsilon)
    rsi = 100.0 - 100.0 / (1.0 + relative_strength)
    rsi = rsi.mask((losses.abs() <= epsilon) & (gains > epsilon), 100.0)
    rsi = rsi.mask((losses.abs() <= epsilon) & (gains.abs() <= epsilon), 50.0)
    features["rsi_14"] = rsi

    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - data["pre_close"]).abs(),
            (data["low"] - data["pre_close"]).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = true_range.rolling(window=14, min_periods=14).mean()
    features["atr_14_to_close"] = _safe_ratio(atr14, data["close"], epsilon)

    for window in (20, 60):
        features[f"maximum_drawdown_{window}d"] = data["close"].rolling(
            window=window,
            min_periods=window,
        ).apply(_window_maximum_drawdown, raw=True)
    for window in (5, 20):
        features[f"up_day_ratio_{window}d"] = (returns > 0).astype(float).rolling(
            window=window,
            min_periods=window,
        ).mean()

    weekday = data["trade_date"].dt.dayofweek
    for day in range(5):
        features[f"weekday_{day}"] = (weekday == day).astype(float)

    feature_frame = pd.DataFrame(features, index=data.index)
    feature_frame = feature_frame.replace([np.inf, -np.inf], np.nan)
    feature_names = tuple(feature_frame.columns)
    output = pd.concat([data, feature_frame], axis=1)
    output["next_return_t1"] = data["close"].shift(-1) / data["close"] - 1.0
    output["target_up_t1"] = np.where(
        output["next_return_t1"].notna(),
        (output["next_return_t1"] > 0).astype(float),
        np.nan,
    )
    output["prediction_for_date"] = data["trade_date"].shift(-1)
    complete_features = output.loc[output[list(feature_names)].notna().all(axis=1)].copy().reset_index(drop=True)
    evaluation = complete_features.loc[complete_features["target_up_t1"].notna()].copy().reset_index(drop=True)
    evaluation["target_up_t1"] = evaluation["target_up_t1"].astype(int)
    return FeatureSet(
        frame=complete_features,
        evaluation_frame=evaluation,
        feature_names=feature_names,
        dictionary=feature_dictionary(),
    )


def feature_dictionary() -> list[dict[str, str]]:
    available = "交易日t收盘并取得完整日线后；仅使用t及以前数据"
    rows = [
        ("close_return_1d", "close[t] / pre_close[t] - 1"),
        ("open_gap", "open[t] / pre_close[t] - 1"),
        ("intraday_return", "close[t] / open[t] - 1"),
        ("high_low_range", "(high[t] - low[t]) / pre_close[t]"),
        ("upper_shadow", "(high[t] - max(open[t], close[t])) / pre_close[t]"),
        ("lower_shadow", "(min(open[t], close[t]) - low[t]) / pre_close[t]"),
        ("close_position", "(close[t] - low[t]) / max(high[t] - low[t], epsilon)"),
        ("log_volume", "log1p(vol[t])"),
        ("log_amount", "log1p(amount[t])"),
        ("volume_change_1d", "vol[t] / vol[t-1] - 1"),
        ("amount_change_1d", "amount[t] / amount[t-1] - 1"),
    ]
    rows.extend((f"cumulative_return_{window}d", f"close[t] / close[t-{window}] - 1") for window in RETURN_WINDOWS)
    rows.extend(
        (f"return_volatility_{window}d", f"population std of close_return_1d over trailing {window} sessions")
        for window in VOLATILITY_WINDOWS
    )
    rows.extend((f"close_to_ma_{window}d", f"close[t] / trailing_mean(close,{window}) - 1") for window in MA_WINDOWS)
    for window in (5, 20):
        rows.append((f"volume_to_ma_{window}d", f"vol[t] / trailing_mean(vol,{window})"))
        rows.append((f"amount_to_ma_{window}d", f"amount[t] / trailing_mean(amount,{window})"))
    rows.extend(
        [
            ("rsi_14", "100 - 100/(1 + trailing_mean(gain,14)/trailing_mean(loss,14))"),
            ("atr_14_to_close", "trailing_mean(true_range,14) / close[t]"),
            ("maximum_drawdown_20d", "minimum path drawdown inside trailing 20 sessions"),
            ("maximum_drawdown_60d", "minimum path drawdown inside trailing 60 sessions"),
            ("up_day_ratio_5d", "share of close_return_1d > 0 in trailing 5 sessions"),
            ("up_day_ratio_20d", "share of close_return_1d > 0 in trailing 20 sessions"),
        ]
    )
    rows.extend((f"weekday_{day}", f"1 when trade_date weekday is {day}, else 0") for day in range(5))
    return [
        {"feature": name, "formula": formula, "available_at": available, "uses_future_data": "false"}
        for name, formula in rows
    ]


def _safe_ratio(numerator: pd.Series, denominator: pd.Series, epsilon: float) -> pd.Series:
    valid = denominator.abs() > epsilon
    return numerator.where(valid) / denominator.where(valid)


def _window_maximum_drawdown(values: np.ndarray) -> float:
    running_high = np.maximum.accumulate(values)
    drawdowns = values / running_high - 1.0
    return float(drawdowns.min())
