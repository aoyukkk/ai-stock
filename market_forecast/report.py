from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from market_forecast.config import DecisionConfig, ExperimentConfig
from market_forecast.metrics import calibration_table


REQUIRED_PREDICTION_COLUMNS = [
    "trade_date",
    "prediction_for_date",
    "fold_id",
    "seed",
    "actual_return",
    "actual_direction",
    "predicted_probability",
    "predicted_direction",
    "is_correct",
    "majority_prediction",
    "persistence_prediction",
    "momentum5_prediction",
]


def determine_decision(summary: dict[str, Any], config: DecisionConfig, *, quick_test: bool) -> dict[str, Any]:
    accuracy = float(summary["gru_accuracy"])
    balanced = float(summary["gru_balanced_accuracy"])
    best_accuracy = float(summary["best_baseline_accuracy"])
    best_balanced = float(summary["best_baseline_balanced_accuracy"])
    best_brier = float(summary["best_baseline_brier_score"])
    improvement = float(summary["accuracy_improvement"])
    lower, upper = (float(value) for value in summary["improvement_ci_95"])
    p_value = float(summary["p_value"])
    brier = float(summary["brier_score"])
    folds_ratio = float(summary["folds_beating_baseline_ratio"])
    seed_std = float(summary["seed_std"])
    if quick_test:
        decision = "INVALID_EXPERIMENT"
        reason = "quick-test 仅验证代码流程，禁止形成正式可行性结论。"
    else:
        promising = all(
            [
                improvement >= config.minimum_accuracy_improvement,
                lower > 0,
                p_value < config.significance_level,
                balanced > best_balanced,
                brier < best_brier,
                folds_ratio >= config.minimum_folds_beating_baseline_ratio,
                seed_std <= config.maximum_seed_accuracy_std,
            ]
        )
        no_edge = all(
            [
                config.near_random_lower <= accuracy <= config.near_random_upper or accuracy <= best_accuracy,
                improvement < config.minimum_accuracy_improvement,
                lower <= 0 <= upper,
                p_value >= config.significance_level,
                folds_ratio <= 0.50,
                balanced <= best_balanced,
                brier >= best_brier,
            ]
        )
        if promising:
            decision = "PROMISING_REQUIRES_CONFIRMATION"
            reason = "固定方案在严格样本外检验中同时满足提升、显著性、校准、年度稳定性和种子稳定性门槛；仅支持独立复验。"
        elif no_edge:
            decision = "NO_PREDICTIVE_EDGE_END_PROJECT"
            reason = "仅使用上证综指自身日线未发现可重复且统计确认的次日方向优势。"
        else:
            decision = "WEAK_OR_UNSTABLE_EDGE"
            reason = "结果未同时满足确认门槛，存在提升不足、统计不显著、年度不稳定、校准不足或种子离散中的至少一项。"
    return {
        "decision": decision,
        "gru_accuracy": accuracy,
        "gru_balanced_accuracy": balanced,
        "best_baseline_name": str(summary["best_baseline_name"]),
        "best_baseline_accuracy": best_accuracy,
        "accuracy_improvement": improvement,
        "improvement_ci_95": [lower, upper],
        "p_value": p_value,
        "brier_score": brier,
        "folds_beating_baseline_ratio": folds_ratio,
        "seed_std": seed_std,
        "reason": reason,
    }


def write_report_bundle(
    output_dir: Path,
    *,
    config: ExperimentConfig,
    experiment_config: dict[str, Any],
    data_quality_report: dict[str, Any],
    feature_dictionary: list[dict[str, Any]],
    fold_boundaries: pd.DataFrame,
    predictions: pd.DataFrame,
    fold_metrics: pd.DataFrame,
    seed_metrics: pd.DataFrame,
    baseline_metrics: pd.DataFrame,
    bootstrap_results: dict[str, Any],
    summary_metrics: dict[str, Any],
    final_decision: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "experiment_config.json", experiment_config)
    _write_json(output_dir / "data_quality_report.json", data_quality_report)
    pd.DataFrame(feature_dictionary).to_csv(output_dir / "feature_dictionary.csv", index=False, encoding="utf-8-sig")
    fold_boundaries.to_csv(output_dir / "fold_boundaries.csv", index=False, encoding="utf-8-sig")
    predictions.loc[:, REQUIRED_PREDICTION_COLUMNS].to_csv(
        output_dir / "predictions.csv",
        index=False,
        encoding="utf-8-sig",
    )
    fold_metrics.to_csv(output_dir / "fold_metrics.csv", index=False, encoding="utf-8-sig")
    seed_metrics.to_csv(output_dir / "seed_metrics.csv", index=False, encoding="utf-8-sig")
    baseline_metrics.to_csv(output_dir / "baseline_metrics.csv", index=False, encoding="utf-8-sig")
    _write_json(output_dir / "bootstrap_results.json", bootstrap_results)
    _write_json(output_dir / "summary_metrics.json", summary_metrics)
    _write_json(output_dir / "final_decision.json", final_decision)
    _plot_accuracy_by_year(output_dir, fold_metrics, baseline_metrics)
    _plot_calibration(output_dir, predictions)
    _plot_gru_vs_baselines(output_dir, summary_metrics)
    _plot_confusion_matrix(output_dir, predictions, config.model.probability_threshold)
    (output_dir / "final_report.md").write_text(
        _final_report_markdown(config, experiment_config, data_quality_report, summary_metrics, final_decision),
        encoding="utf-8",
    )


def write_invalid_bundle(
    output_dir: Path,
    *,
    experiment_config: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    decision = {
        "decision": "INVALID_EXPERIMENT",
        "gru_accuracy": 0.0,
        "gru_balanced_accuracy": 0.0,
        "best_baseline_name": "unavailable",
        "best_baseline_accuracy": 0.0,
        "accuracy_improvement": 0.0,
        "improvement_ci_95": [0.0, 0.0],
        "p_value": 1.0,
        "brier_score": 0.0,
        "folds_beating_baseline_ratio": 0.0,
        "seed_std": 0.0,
        "reason": reason,
    }
    _write_json(output_dir / "experiment_config.json", experiment_config)
    _write_json(output_dir / "data_quality_report.json", {"status": "FAIL", "failures": [reason]})
    pd.DataFrame(columns=["feature", "formula", "available_at", "uses_future_data"]).to_csv(
        output_dir / "feature_dictionary.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame().to_csv(output_dir / "fold_boundaries.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(columns=REQUIRED_PREDICTION_COLUMNS).to_csv(output_dir / "predictions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame().to_csv(output_dir / "fold_metrics.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame().to_csv(output_dir / "seed_metrics.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame().to_csv(output_dir / "baseline_metrics.csv", index=False, encoding="utf-8-sig")
    _write_json(output_dir / "bootstrap_results.json", {"status": "NOT_RUN", "reason": reason})
    _write_json(output_dir / "summary_metrics.json", {"status": "INVALID_EXPERIMENT", "reason": reason})
    _write_json(output_dir / "final_decision.json", decision)
    for name in ("accuracy_by_year.png", "probability_calibration.png", "gru_vs_baselines.png", "confusion_matrix.png"):
        _plot_invalid(output_dir / name, reason)
    (output_dir / "final_report.md").write_text(
        "# Market Direction GRU Feasibility Experiment\n\n"
        "## Final decision\n\n`INVALID_EXPERIMENT`\n\n"
        f"Reason: {reason}\n",
        encoding="utf-8",
    )
    return decision


def _plot_accuracy_by_year(output_dir: Path, fold_metrics: pd.DataFrame, baseline_metrics: pd.DataFrame) -> None:
    gru = fold_metrics.groupby("test_year", as_index=False)["accuracy"].mean().sort_values("test_year")
    base = (
        baseline_metrics[baseline_metrics["scope"] == "fold"]
        .groupby(["test_year", "baseline_name"], as_index=False)["accuracy"]
        .mean()
    )
    figure, axis = plt.subplots(figsize=(10, 5.5))
    axis.plot(gru["test_year"], gru["accuracy"], marker="o", linewidth=2, label="GRU seed mean")
    for name, group in base.groupby("baseline_name"):
        axis.plot(group["test_year"], group["accuracy"], marker=".", alpha=0.75, label=name)
    axis.axhline(0.5, color="#777777", linestyle="--", linewidth=1)
    axis.set_title("Out-of-sample accuracy by test year")
    axis.set_xlabel("Test year")
    axis.set_ylabel("Accuracy")
    axis.set_ylim(0.35, 0.65)
    axis.grid(alpha=0.2)
    axis.legend(ncol=2)
    figure.tight_layout()
    figure.savefig(output_dir / "accuracy_by_year.png", dpi=160)
    plt.close(figure)


def _plot_calibration(output_dir: Path, predictions: pd.DataFrame) -> None:
    daily = predictions.groupby("prediction_for_date", as_index=False).agg(
        actual_direction=("actual_direction", "first"),
        predicted_probability=("predicted_probability", "mean"),
    )
    rows = calibration_table(daily["actual_direction"].to_numpy(), daily["predicted_probability"].to_numpy())
    figure, axis = plt.subplots(figsize=(6.5, 6))
    axis.plot([0, 1], [0, 1], linestyle="--", color="#777777", label="Perfect calibration")
    if rows:
        axis.plot(
            [row["mean_predicted_probability"] for row in rows],
            [row["actual_up_rate"] for row in rows],
            marker="o",
            linewidth=2,
            label="GRU seed-mean probability",
        )
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.set_xlabel("Mean predicted probability")
    axis.set_ylabel("Observed up rate")
    axis.set_title("Probability calibration")
    axis.grid(alpha=0.2)
    axis.legend()
    figure.tight_layout()
    figure.savefig(output_dir / "probability_calibration.png", dpi=160)
    plt.close(figure)


def _plot_gru_vs_baselines(output_dir: Path, summary: dict[str, Any]) -> None:
    labels = ["GRU seed mean", "Majority", "Persistence", "Momentum 5d"]
    baseline = summary["baseline_overall"]
    values = [
        summary["gru_accuracy"],
        baseline["majority"]["accuracy"],
        baseline["persistence"]["accuracy"],
        baseline["momentum5"]["accuracy"],
    ]
    figure, axis = plt.subplots(figsize=(8, 5))
    colors = ["#2F5597", "#A5A5A5", "#ED7D31", "#70AD47"]
    bars = axis.bar(labels, values, color=colors)
    axis.axhline(0.5, color="#777777", linestyle="--", linewidth=1)
    axis.set_ylim(min(0.4, min(values) - 0.03), max(0.6, max(values) + 0.03))
    axis.set_ylabel("Accuracy")
    axis.set_title("GRU versus frozen naive baselines")
    for bar, value in zip(bars, values):
        axis.text(bar.get_x() + bar.get_width() / 2, value + 0.005, f"{value:.3f}", ha="center")
    figure.tight_layout()
    figure.savefig(output_dir / "gru_vs_baselines.png", dpi=160)
    plt.close(figure)


def _plot_confusion_matrix(output_dir: Path, predictions: pd.DataFrame, threshold: float) -> None:
    daily = predictions.groupby("prediction_for_date", as_index=False).agg(
        actual_direction=("actual_direction", "first"),
        predicted_probability=("predicted_probability", "mean"),
    )
    actual = daily["actual_direction"].to_numpy(dtype=int)
    predicted = (daily["predicted_probability"].to_numpy(dtype=float) >= threshold).astype(int)
    matrix = np.zeros((2, 2), dtype=int)
    for true, guess in zip(actual, predicted):
        matrix[true, guess] += 1
    figure, axis = plt.subplots(figsize=(5.5, 5))
    image = axis.imshow(matrix, cmap="Blues")
    for row in range(2):
        for column in range(2):
            axis.text(column, row, str(matrix[row, column]), ha="center", va="center", fontsize=13)
    axis.set_xticks([0, 1], labels=["Down", "Up"])
    axis.set_yticks([0, 1], labels=["Down", "Up"])
    axis.set_xlabel("Predicted")
    axis.set_ylabel("Actual")
    axis.set_title("Seed-mean probability confusion matrix")
    figure.colorbar(image, ax=axis, fraction=0.046)
    figure.tight_layout()
    figure.savefig(output_dir / "confusion_matrix.png", dpi=160)
    plt.close(figure)


def _plot_invalid(path: Path, reason: str) -> None:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.axis("off")
    axis.text(0.5, 0.6, "INVALID_EXPERIMENT", ha="center", va="center", fontsize=18, weight="bold")
    axis.text(0.5, 0.4, reason[:180], ha="center", va="center", wrap=True)
    figure.tight_layout()
    figure.savefig(path, dpi=140)
    plt.close(figure)


def _final_report_markdown(
    config: ExperimentConfig,
    experiment: dict[str, Any],
    quality: dict[str, Any],
    summary: dict[str, Any],
    decision: dict[str, Any],
) -> str:
    ci = decision["improvement_ci_95"]
    return "\n".join(
        [
            "# Market Direction GRU Feasibility Experiment",
            "",
            "本报告是隔离研究实验，不构成投资建议，不接入交易系统。",
            "",
            "## Frozen design",
            "",
            f"- Index: `{config.data.index_code}`",
            f"- Data: {quality.get('date_start')} to {quality.get('date_end')}",
            f"- Lookback: {config.sequence.lookback_days} trading sessions",
            f"- Seeds: {list(config.seeds)}",
            f"- OOS folds: {summary.get('fold_count')}",
            f"- Device: {experiment.get('resolved_device')}",
            "",
            "## Formal results",
            "",
            f"- GRU accuracy (seed mean): {decision['gru_accuracy']:.6f}",
            f"- GRU balanced accuracy (seed mean): {decision['gru_balanced_accuracy']:.6f}",
            f"- Best baseline: {decision['best_baseline_name']}",
            f"- Best baseline accuracy: {decision['best_baseline_accuracy']:.6f}",
            f"- Accuracy improvement: {decision['accuracy_improvement']:.6f}",
            f"- Improvement 95% CI: [{ci[0]:.6f}, {ci[1]:.6f}]",
            f"- One-sided p-value: {decision['p_value']:.6f}",
            f"- Brier Score: {decision['brier_score']:.6f}",
            f"- Folds beating baseline: {decision['folds_beating_baseline_ratio']:.3f}",
            f"- Seed accuracy std: {decision['seed_std']:.6f}",
            "",
            "## Final decision",
            "",
            f"`{decision['decision']}`",
            "",
            decision["reason"],
            "",
            "## Limitations",
            "",
            "- 仅使用上证综指自身日线，不包含新闻、资金流、板块、宏观或分钟数据。",
            "- 概率阈值和超参数在测试集之前冻结；本阶段不根据结果调整。",
            "- 当前年度若未结束，作为 partial fold 单独标记。",
        ]
    ) + "\n"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
