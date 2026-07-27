from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd
import sklearn
import torch

from market_forecast.baselines import BASELINE_NAMES, baseline_predictions
from market_forecast.bootstrap import moving_block_bootstrap
from market_forecast.config import ExperimentConfig, load_experiment_config
from market_forecast.data_loader import MarketForecastDataLoader
from market_forecast.features import build_features
from market_forecast.gru_model import predict_probabilities, resolve_device, train_gru
from market_forecast.leakage_checks import run_leakage_checks
from market_forecast.metrics import calibration_table, classification_metrics, hard_prediction_metrics, metrics_record
from market_forecast.report import determine_decision, write_invalid_bundle, write_report_bundle
from market_forecast.sequence_dataset import build_sequences, fit_training_scaler, transform_sequences
from market_forecast.walk_forward import WalkForwardFold, build_walk_forward_folds


SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_CONFIG = ROOT / "config" / "market_direction_experiment.yaml"


def run_experiment(
    config: ExperimentConfig,
    *,
    output_dir: Path,
    run_id: str,
    refresh_data: bool,
) -> dict[str, Any]:
    config.validate()
    device = resolve_device(config.device)
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    experiment_config = _experiment_config_payload(config, run_id, device)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "experiment_config.json").write_text(
        json.dumps(experiment_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    loader = MarketForecastDataLoader(cache_root=config.data.cache_root)
    loaded = loader.load(
        index_code=config.data.index_code,
        start_date=config.data.start_date,
        end_date=config.data.end_date,
        exchange=config.data.exchange,
        market_close_time=config.data.market_close_time,
        refresh_data=refresh_data,
    )
    feature_set = build_features(loaded.quality.frame, epsilon=config.features.epsilon)
    leakage_report = run_leakage_checks(
        loaded.quality.frame,
        feature_set,
        loaded.quality.calendar,
        epsilon=config.features.epsilon,
    )
    sequences = build_sequences(
        feature_set.frame,
        feature_set.feature_names,
        lookback_days=config.sequence.lookback_days,
    )
    folds = build_walk_forward_folds(sequences.metadata, config.walk_forward)
    if config.quick_test:
        folds = folds[-1:]

    fold_records = pd.DataFrame([fold.boundary_record() for fold in folds])
    prediction_rows: list[dict[str, Any]] = []
    fold_metric_rows: list[dict[str, Any]] = []
    baseline_metric_rows: list[dict[str, Any]] = []

    for fold_number, fold in enumerate(folds, start=1):
        print(
            f"[market-direction] fold={fold.fold_id} {fold_number}/{len(folds)} "
            f"train={len(fold.train_indexes)} validation={len(fold.validation_indexes)} test={len(fold.test_indexes)}"
        )
        train_x, train_y, train_meta = sequences.take(fold.train_indexes)
        validation_x, validation_y, validation_meta = sequences.take(fold.validation_indexes)
        test_x, test_y, test_meta = sequences.take(fold.test_indexes)
        _assert_fold_boundaries(train_meta, validation_meta, test_meta)
        scaler = fit_training_scaler(train_x)
        scaler_dir = output_dir / "scalers" / fold.fold_id
        scaler_dir.mkdir(parents=True, exist_ok=True)
        joblib.dump(scaler, scaler_dir / "standard_scaler.joblib")
        scaler_metadata = {
            "fold_id": fold.fold_id,
            "fitted_on": "training_sequences_only",
            "train_prediction_date_start": train_meta["prediction_for_date"].min().date().isoformat(),
            "train_prediction_date_end": train_meta["prediction_for_date"].max().date().isoformat(),
            "train_sequence_count": int(len(train_x)),
            "lookback_days": int(train_x.shape[1]),
            "feature_count": int(train_x.shape[2]),
            "n_samples_seen": int(scaler.n_samples_seen_),
            "validation_or_test_fit_used": False,
        }
        (scaler_dir / "scaler_metadata.json").write_text(
            json.dumps(scaler_metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        scaled_train = transform_sequences(train_x, scaler)
        scaled_validation = transform_sequences(validation_x, scaler)
        scaled_test = transform_sequences(test_x, scaler)

        baseline_values = baseline_predictions(train_y, test_meta)
        for baseline_name, values in baseline_values.items():
            metrics = hard_prediction_metrics(test_y, values)
            baseline_metric_rows.append(
                metrics_record(
                    {
                        "scope": "fold",
                        "fold_id": fold.fold_id,
                        "test_year": fold.test_year,
                        "is_partial": fold.is_partial,
                        "baseline_name": baseline_name,
                    },
                    metrics,
                )
            )

        for seed_number, seed in enumerate(config.seeds, start=1):
            print(
                f"[market-direction] fold={fold.fold_id} seed={seed} "
                f"{seed_number}/{len(config.seeds)} device={device.type}"
            )
            training = train_gru(
                scaled_train,
                train_y,
                scaled_validation,
                validation_y,
                config=config.model,
                seed=seed,
                device=device,
            )
            probabilities = predict_probabilities(training.model, scaled_test, device)
            predictions = (probabilities >= config.model.probability_threshold).astype(int)
            metrics = classification_metrics(test_y, probabilities, threshold=config.model.probability_threshold)
            fold_metric_rows.append(
                metrics_record(
                    {
                        "fold_id": fold.fold_id,
                        "test_year": fold.test_year,
                        "is_partial": fold.is_partial,
                        "seed": seed,
                        "best_epoch": training.best_epoch,
                        "epochs_trained": training.epochs_trained,
                        "best_validation_loss": training.best_validation_loss,
                        "train_positive_ratio": training.train_positive_ratio,
                        "pos_weight": training.positive_weight,
                    },
                    metrics,
                )
            )
            model_dir = output_dir / "models" / fold.fold_id
            model_dir.mkdir(parents=True, exist_ok=True)
            torch.save(
                {
                    "state_dict": {key: value.detach().cpu() for key, value in training.model.state_dict().items()},
                    "input_size": int(scaled_train.shape[-1]),
                    "fold_id": fold.fold_id,
                    "seed": seed,
                    "best_epoch": training.best_epoch,
                    "model_config": config.to_dict()["model"],
                },
                model_dir / f"seed_{seed}.pt",
            )
            pd.DataFrame(training.history).to_csv(
                model_dir / f"seed_{seed}_history.csv",
                index=False,
                encoding="utf-8-sig",
            )
            for row_index, meta in test_meta.iterrows():
                prediction_rows.append(
                    {
                        "trade_date": meta["trade_date"].date().isoformat(),
                        "prediction_for_date": meta["prediction_for_date"].date().isoformat(),
                        "fold_id": fold.fold_id,
                        "seed": seed,
                        "actual_return": float(meta["actual_return"]),
                        "actual_direction": int(meta["actual_direction"]),
                        "predicted_probability": float(probabilities[row_index]),
                        "predicted_direction": int(predictions[row_index]),
                        "is_correct": int(predictions[row_index] == int(meta["actual_direction"])),
                        "majority_prediction": int(baseline_values["majority"][row_index]),
                        "persistence_prediction": int(baseline_values["persistence"][row_index]),
                        "momentum5_prediction": int(baseline_values["momentum5"][row_index]),
                    }
                )

    predictions = pd.DataFrame(prediction_rows)
    fold_metrics = pd.DataFrame(fold_metric_rows)
    baseline_metrics = pd.DataFrame(baseline_metric_rows)
    _assert_prediction_alignment(predictions, config.seeds)
    seed_metrics = _build_seed_metrics(predictions, config)
    baseline_metrics, baseline_overall = _append_overall_baseline_metrics(baseline_metrics, predictions)
    summary_metrics, bootstrap_results = _build_summary(
        predictions,
        fold_metrics,
        seed_metrics,
        baseline_metrics,
        baseline_overall,
        config,
    )
    final_decision = determine_decision(summary_metrics, config.decision, quick_test=config.quick_test)
    data_quality_report = dict(loaded.quality.report)
    data_quality_report.update(
        {
            "cache_paths": loaded.cache_paths,
            "provider_diagnostics": loaded.provider_diagnostics,
            "feature_complete_row_count": int(len(feature_set.frame)),
            "historical_labeled_row_count": int(len(feature_set.evaluation_frame)),
            "sequence_sample_count": int(len(sequences.targets)),
            "feature_count": int(len(feature_set.feature_names)),
            "latest_row_future_prediction_only": feature_set.frame.iloc[-1]["trade_date"].date().isoformat(),
            "leakage_checks": leakage_report,
        }
    )
    experiment_config.update(
        {
            "data_date_start": loaded.quality.report["date_start"],
            "data_date_end": loaded.quality.report["date_end"],
            "latest_valid_trade_date": loaded.expected_latest_trade_date.date().isoformat(),
            "formal_oos_test_start": fold_records["test_start"].min(),
            "formal_oos_test_end": fold_records["test_end"].max(),
            "fold_count": int(len(folds)),
            "feature_count": int(len(feature_set.feature_names)),
            "test_results_used_for_tuning": False,
        }
    )
    write_report_bundle(
        output_dir,
        config=config,
        experiment_config=experiment_config,
        data_quality_report=data_quality_report,
        feature_dictionary=feature_set.dictionary,
        fold_boundaries=fold_records,
        predictions=predictions,
        fold_metrics=fold_metrics,
        seed_metrics=seed_metrics,
        baseline_metrics=baseline_metrics,
        bootstrap_results=bootstrap_results,
        summary_metrics=summary_metrics,
        final_decision=final_decision,
    )
    _print_console_summary(output_dir, experiment_config, summary_metrics, final_decision)
    return {
        "output_dir": str(output_dir.resolve()),
        "experiment_config": experiment_config,
        "summary_metrics": summary_metrics,
        "final_decision": final_decision,
    }


def _build_seed_metrics(predictions: pd.DataFrame, config: ExperimentConfig) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for seed, group in predictions.groupby("seed", sort=True):
        metrics = classification_metrics(
            group["actual_direction"].to_numpy(),
            group["predicted_probability"].to_numpy(),
            threshold=config.model.probability_threshold,
        )
        rows.append(metrics_record({"scope": "overall", "seed": int(seed)}, metrics))
    return pd.DataFrame(rows)


def _append_overall_baseline_metrics(
    baseline_metrics: pd.DataFrame,
    predictions: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    unique = predictions.sort_values(["prediction_for_date", "seed"]).drop_duplicates("prediction_for_date")
    actual = unique["actual_direction"].to_numpy(dtype=int)
    rows: list[dict[str, Any]] = []
    overall: dict[str, dict[str, Any]] = {}
    for name in BASELINE_NAMES:
        values = unique[f"{name}_prediction"].to_numpy(dtype=int)
        metrics = hard_prediction_metrics(actual, values)
        overall[name] = metrics
        rows.append(
            metrics_record(
                {"scope": "overall", "fold_id": "ALL", "test_year": "ALL", "is_partial": False, "baseline_name": name},
                metrics,
            )
        )
    return pd.concat([baseline_metrics, pd.DataFrame(rows)], ignore_index=True), overall


def _build_summary(
    predictions: pd.DataFrame,
    fold_metrics: pd.DataFrame,
    seed_metrics: pd.DataFrame,
    baseline_metrics: pd.DataFrame,
    baseline_overall: dict[str, dict[str, Any]],
    config: ExperimentConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    best_baseline_name = max(BASELINE_NAMES, key=lambda name: baseline_overall[name]["accuracy"])
    best_baseline = baseline_overall[best_baseline_name]
    daily = predictions.groupby("prediction_for_date", as_index=False).agg(
        actual_direction=("actual_direction", "first"),
        predicted_probability=("predicted_probability", "mean"),
        gru_correct_fraction=("is_correct", "mean"),
        fold_id=("fold_id", "first"),
        majority_prediction=("majority_prediction", "first"),
        persistence_prediction=("persistence_prediction", "first"),
        momentum5_prediction=("momentum5_prediction", "first"),
    )
    baseline_prediction = daily[f"{best_baseline_name}_prediction"].to_numpy(dtype=int)
    baseline_correct = (baseline_prediction == daily["actual_direction"].to_numpy(dtype=int)).astype(float)
    bootstrap = moving_block_bootstrap(
        daily["gru_correct_fraction"].to_numpy(dtype=float),
        baseline_correct,
        block_length=config.bootstrap.block_length,
        resamples=config.bootstrap.resamples,
        random_seed=config.bootstrap.random_seed,
    )
    gru_fold_mean = fold_metrics.groupby("fold_id")["accuracy"].mean()
    fold_baselines = baseline_metrics[baseline_metrics["scope"] == "fold"].groupby("fold_id")["accuracy"].max()
    aligned = pd.concat([gru_fold_mean.rename("gru"), fold_baselines.rename("baseline")], axis=1).dropna()
    folds_ratio = float((aligned["gru"] > aligned["baseline"]).mean())
    ensemble_metrics = classification_metrics(
        daily["actual_direction"].to_numpy(dtype=int),
        daily["predicted_probability"].to_numpy(dtype=float),
        threshold=config.model.probability_threshold,
    )
    seed_accuracies = seed_metrics["accuracy"].to_numpy(dtype=float)
    accuracy = float(seed_accuracies.mean())
    balanced_accuracy = float(seed_metrics["balanced_accuracy"].mean())
    brier_score = float(seed_metrics["brier_score"].mean())
    summary = {
        "sample_count": int(len(daily)),
        "fold_count": int(fold_metrics["fold_id"].nunique()),
        "seed_count": int(len(seed_metrics)),
        "gru_accuracy": accuracy,
        "gru_accuracy_median": float(np.median(seed_accuracies)),
        "gru_balanced_accuracy": balanced_accuracy,
        "brier_score": brier_score,
        "seed_std": float(np.std(seed_accuracies, ddof=0)),
        "seed_accuracy_min": float(seed_accuracies.min()),
        "seed_accuracy_max": float(seed_accuracies.max()),
        "best_baseline_name": best_baseline_name,
        "best_baseline_accuracy": float(best_baseline["accuracy"]),
        "best_baseline_balanced_accuracy": float(best_baseline["balanced_accuracy"]),
        "best_baseline_brier_score": float(best_baseline["brier_score"]),
        "accuracy_improvement": float(accuracy - best_baseline["accuracy"]),
        "improvement_ci_95": bootstrap["improvement_ci_95"],
        "p_value": bootstrap["one_sided_p_value"],
        "gru_accuracy_ci_95": bootstrap["gru_accuracy_ci_95"],
        "folds_beating_baseline_ratio": folds_ratio,
        "baseline_overall": baseline_overall,
        "ensemble_metrics": ensemble_metrics,
        "probability_calibration": calibration_table(
            daily["actual_direction"].to_numpy(dtype=int),
            daily["predicted_probability"].to_numpy(dtype=float),
        ),
        "accuracy_by_year": _accuracy_by_year(predictions),
        "aggregation_note": "Primary GRU metrics are means across all frozen seeds; bootstrap uses each day's mean seed correctness.",
    }
    return summary, bootstrap


def _accuracy_by_year(predictions: pd.DataFrame) -> list[dict[str, Any]]:
    frame = predictions.copy()
    frame["year"] = pd.to_datetime(frame["prediction_for_date"]).dt.year
    rows: list[dict[str, Any]] = []
    for (year, seed), group in frame.groupby(["year", "seed"]):
        rows.append(
            {
                "year": int(year),
                "seed": int(seed),
                "sample_count": int(len(group)),
                "accuracy": float(group["is_correct"].mean()),
            }
        )
    return rows


def _assert_fold_boundaries(train: pd.DataFrame, validation: pd.DataFrame, test: pd.DataFrame) -> None:
    train_end = pd.to_datetime(train["prediction_for_date"]).max()
    validation_start = pd.to_datetime(validation["prediction_for_date"]).min()
    validation_end = pd.to_datetime(validation["prediction_for_date"]).max()
    test_start = pd.to_datetime(test["prediction_for_date"]).min()
    if not (train_end < validation_start <= validation_end < test_start):
        raise RuntimeError("FOLD_TEMPORAL_ORDER_VIOLATION")


def _assert_prediction_alignment(predictions: pd.DataFrame, seeds: tuple[int, ...]) -> None:
    expected_dates: tuple[str, ...] | None = None
    for seed in seeds:
        group = predictions[predictions["seed"] == seed].sort_values("prediction_for_date")
        dates = tuple(group["prediction_for_date"].astype(str))
        if expected_dates is None:
            expected_dates = dates
        elif dates != expected_dates:
            raise RuntimeError("SEED_TEST_DATE_MISMATCH")
    for column in ("majority_prediction", "persistence_prediction", "momentum5_prediction"):
        distinct = predictions.groupby("prediction_for_date")[column].nunique()
        if (distinct > 1).any():
            raise RuntimeError(f"BASELINE_DATE_ALIGNMENT_MISMATCH:{column}")


def _experiment_config_payload(config: ExperimentConfig, run_id: str, device: torch.device) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": datetime.now(SHANGHAI).isoformat(),
        "phase": config.experiment_name,
        "frozen_before_test_evaluation": True,
        "config": config.to_dict(),
        "resolved_device": device.type,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "torch": torch.__version__,
            "scikit_learn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "production_integration": False,
        "llm_calls": 0,
        "database_writes": 0,
        "trading_actions": 0,
    }


def _print_console_summary(
    output_dir: Path,
    experiment: dict[str, Any],
    summary: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    print("\n=== Market Direction GRU Feasibility Experiment ===")
    print(f"实际数据日期范围: {experiment['data_date_start']} -> {experiment['data_date_end']}")
    print(f"最新有效交易日: {experiment['latest_valid_trade_date']}")
    print(f"总样本数: {summary['sample_count']}")
    print(f"测试区间: {experiment['formal_oos_test_start']} -> {experiment['formal_oos_test_end']}")
    print(
        "GRU总体指标: "
        f"accuracy={summary['gru_accuracy']:.6f}, "
        f"balanced_accuracy={summary['gru_balanced_accuracy']:.6f}, "
        f"brier={summary['brier_score']:.6f}"
    )
    for name in BASELINE_NAMES:
        item = summary["baseline_overall"][name]
        print(f"基线 {name}: accuracy={item['accuracy']:.6f}, balanced_accuracy={item['balanced_accuracy']:.6f}")
    ci = summary["improvement_ci_95"]
    print(f"accuracy提升: {summary['accuracy_improvement']:.6f}")
    print(f"提升95%置信区间: [{ci[0]:.6f}, {ci[1]:.6f}]")
    print(f"单侧p值: {summary['p_value']:.6f}")
    print(f"最终判定: {decision['decision']}")
    print(f"报告路径: {output_dir.resolve()}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Leakage-safe GRU market direction feasibility experiment")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--index-code", default="000001.SH")
    parser.add_argument("--start-date", default="20050101")
    parser.add_argument("--end-date", default="auto")
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 2026])
    parser.add_argument("--refresh-data", action="store_true")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--quick-test", action="store_true")
    return parser.parse_args()


def _apply_overrides(base: ExperimentConfig, args: argparse.Namespace) -> ExperimentConfig:
    config = replace(
        base,
        data=replace(
            base.data,
            index_code=args.index_code,
            start_date=args.start_date,
            end_date=args.end_date,
        ),
        sequence=replace(base.sequence, lookback_days=args.lookback),
        seeds=tuple(args.seeds),
        device=args.device,
        quick_test=bool(args.quick_test),
    )
    if args.quick_test:
        config = replace(
            config,
            seeds=(config.seeds[0],),
            model=replace(config.model, max_epochs=2, early_stopping_patience=1),
            bootstrap=replace(config.bootstrap, resamples=200),
        )
    config.validate()
    return config


def _run_id(config: ExperimentConfig) -> str:
    stamp = datetime.now(SHANGHAI).strftime("%Y%m%dT%H%M%S")
    digest = hashlib.sha256(json.dumps(config.to_dict(), sort_keys=True).encode()).hexdigest()[:8]
    return f"market-direction-{stamp}-{digest}"


def main() -> int:
    args = _parse_args()
    base = load_experiment_config(args.config)
    config = _apply_overrides(base, args)
    run_id = _run_id(config)
    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "reports" / "market_direction" / run_id
    try:
        run_experiment(config, output_dir=output_dir, run_id=run_id, refresh_data=args.refresh_data)
        return 0
    except Exception as exc:
        reason = f"{exc.__class__.__name__}: {exc}"
        experiment_config = {
            "run_id": run_id,
            "created_at": datetime.now(SHANGHAI).isoformat(),
            "phase": config.experiment_name,
            "frozen_before_test_evaluation": True,
            "config": config.to_dict(),
            "production_integration": False,
            "llm_calls": 0,
            "database_writes": 0,
            "trading_actions": 0,
        }
        write_invalid_bundle(output_dir, experiment_config=experiment_config, reason=reason)
        print(f"INVALID_EXPERIMENT: {reason}", file=sys.stderr)
        print(f"报告路径: {output_dir.resolve()}", file=sys.stderr)
        if os.getenv("MARKET_FORECAST_DEBUG", "").strip() == "1":
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
