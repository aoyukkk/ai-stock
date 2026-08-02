from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean, median, pstdev
from typing import Iterable, Mapping, Sequence

from services.ranking_evaluation.metric_calculator import (
    MetricObservation,
    calculate_metric_payload,
    spearman_rank_ic,
)
from services.ranking_evaluation.model_stage_constants import (
    V2_V3_COMPARISON_CONTRACT_MISMATCH,
)


@dataclass(frozen=True)
class StageObservation:
    stock_code: str
    quant_rank: int
    quant_score: float | None
    future_return: float | None
    flash_score: float | None = None
    flash_rank: int | None = None
    selected: bool = False
    risk_action: str | None = None
    event_score: float | None = None
    task_status: str = "SUCCESS"
    schema_status: str = "PASS"
    search_status: str | None = None
    actionable: bool = True


def _spearman_signal(signal: Sequence[float], returns: Sequence[float]) -> float | None:
    if len(signal) < 2 or len(signal) != len(returns):
        return None
    order = sorted(range(len(signal)), key=lambda index: (-signal[index], index))
    ranks = [0] * len(signal)
    for rank, index in enumerate(order, start=1):
        ranks[index] = rank
    observations = [
        MetricObservation(str(index), ranks[index], returns[index])
        for index in range(len(signal))
    ]
    return spearman_rank_ic(observations, top_n=len(signal))


def actionability_status(
    output_available_at: datetime | None,
    next_market_open_at: datetime | None,
) -> str:
    if output_available_at is None or next_market_open_at is None:
        return "OUTPUT_TIME_MISSING"
    if output_available_at.tzinfo is None and next_market_open_at.tzinfo is not None:
        output_available_at = output_available_at.replace(
            tzinfo=next_market_open_at.tzinfo
        )
    elif next_market_open_at.tzinfo is None and output_available_at.tzinfo is not None:
        next_market_open_at = next_market_open_at.replace(
            tzinfo=output_available_at.tzinfo
        )
    if output_available_at < next_market_open_at:
        return "ACTIONABLE_BEFORE_NEXT_OPEN"
    return "LATE_OUTPUT_NON_ACTIONABLE"


def quant_metrics(rows: Iterable[StageObservation]) -> dict[str, object]:
    values = [row for row in rows if row.future_return is not None]
    base = calculate_metric_payload(
        [
            MetricObservation(row.stock_code, row.quant_rank, float(row.future_return))
            for row in values
        ],
        top_n=100,
    )
    score_rows = [
        row for row in values if row.quant_score is not None
    ]
    base["quant_score_ic"] = _spearman_signal(
        [float(row.quant_score) for row in score_rows],
        [float(row.future_return) for row in score_rows],
    )
    return base


def flash_metrics(
    rows: Iterable[StageObservation],
    *,
    cohort_size: int = 100,
    nominal_selected: int = 20,
    tail_loss_threshold: float = -0.05,
    actionable_only: bool = False,
) -> dict[str, object]:
    all_rows = list(rows)
    scope = [row for row in all_rows if not actionable_only or row.actionable]
    score_rows = [
        row for row in scope
        if row.flash_score is not None and row.future_return is not None
    ]
    complete_ranking = (
        len(scope) == cohort_size
        and all(row.flash_rank is not None for row in scope)
        and len({row.flash_rank for row in scope}) == cohort_size
    )
    rank_rows = [
        row for row in scope
        if row.flash_rank is not None and row.future_return is not None
    ]
    selected = [row for row in scope if row.selected and row.future_return is not None]
    unselected = [row for row in scope if not row.selected and row.future_return is not None]
    quant_top = [row for row in scope if row.quant_rank <= nominal_selected and row.future_return is not None]
    promoted = [
        row for row in scope
        if row.selected and row.quant_rank > nominal_selected and row.future_return is not None
    ]
    demoted = [
        row for row in scope
        if not row.selected and row.quant_rank <= nominal_selected and row.future_return is not None
    ]
    event_rows = [
        row for row in scope
        if row.event_score is not None and row.future_return is not None
    ]
    selected_returns = [float(row.future_return) for row in selected]
    unselected_returns = [float(row.future_return) for row in unselected]
    quant_returns = [float(row.future_return) for row in quant_top]
    promoted_returns = [float(row.future_return) for row in promoted]
    demoted_returns = [float(row.future_return) for row in demoted]
    selected_mean = _mean(selected_returns)
    unselected_mean = _mean(unselected_returns)
    quant_mean = _mean(quant_returns)
    promoted_mean = _mean(promoted_returns)
    demoted_mean = _mean(demoted_returns)
    realized_top = {
        row.stock_code
        for row in sorted(
            [row for row in scope if row.future_return is not None],
            key=lambda row: float(row.future_return),
            reverse=True,
        )[:nominal_selected]
    }
    selected_codes = {row.stock_code for row in scope if row.selected}
    quant_codes = {row.stock_code for row in scope if row.quant_rank <= nominal_selected}
    return {
        "flash_score_ic": _spearman_signal(
            [float(row.flash_score) for row in score_rows],
            [float(row.future_return) for row in score_rows],
        ),
        "flash_score_valid_count": len(score_rows),
        "scoring_coverage_ratio": len(score_rows) / cohort_size if cohort_size else 0.0,
        "flash_rank_ic": (
            _spearman_signal(
                [float(cohort_size + 1 - int(row.flash_rank)) for row in rank_rows],
                [float(row.future_return) for row in rank_rows],
            )
            if complete_ranking
            else None
        ),
        "flash_rank_status": "COMPLETE" if complete_ranking else "FULL_RANKING_NOT_AVAILABLE",
        "event_score_ic": _spearman_signal(
            [float(row.event_score) for row in event_rows],
            [float(row.future_return) for row in event_rows],
        ),
        "selected_mean_return": selected_mean,
        "unselected_mean_return": unselected_mean,
        "selection_spread": _difference(selected_mean, unselected_mean),
        "quant_top20_mean_return": quant_mean,
        "flash_top20_mean_return": selected_mean,
        "incremental_lift": _difference(selected_mean, quant_mean),
        "promoted_mean_return": promoted_mean,
        "demoted_mean_return": demoted_mean,
        "promote_demote_spread": _difference(promoted_mean, demoted_mean),
        "promoted_count": len([row for row in scope if row.selected and row.quant_rank > nominal_selected]),
        "demoted_count": len([row for row in scope if not row.selected and row.quant_rank <= nominal_selected]),
        "promoted_valid_count": len(promoted),
        "demoted_valid_count": len(demoted),
        "overlap_count": len(selected_codes & quant_codes),
        "replacement_count": len(selected_codes - quant_codes),
        "top20_overlap_rate": len(selected_codes & quant_codes) / nominal_selected,
        "nominal_denominator": nominal_selected,
        "actual_selected_count": len(selected_codes),
        "selected_valid_count": len(selected_returns),
        "unselected_valid_count": len(unselected_returns),
        "selected_coverage_ratio": len(selected_returns) / max(1, len(selected_codes)),
        "selected_median_return": median(selected_returns) if selected_returns else None,
        "selected_win_rate": _ratio(selected_returns, lambda value: value > 0),
        "selected_loss_rate": _ratio(selected_returns, lambda value: value < 0),
        "selected_return_std": pstdev(selected_returns) if len(selected_returns) > 1 else None,
        "selected_worst_return": min(selected_returns) if selected_returns else None,
        "selected_best_return": max(selected_returns) if selected_returns else None,
        "selected_positive_count": sum(value > 0 for value in selected_returns),
        "selected_negative_count": sum(value < 0 for value in selected_returns),
        "realized_top20_capture": len(selected_codes & realized_top) / nominal_selected,
        "positive_precision": _ratio(selected_returns, lambda value: value > 0),
        "tail_loss_rate": _ratio(
            selected_returns, lambda value: value < tail_loss_threshold
        ),
        "blocked_avoided_loss": _mean(
            [
                -float(row.future_return)
                for row in scope
                if row.risk_action == "BLOCK"
                and row.future_return is not None
                and row.future_return < 0
            ]
        ),
        "blocked_missed_gain": _mean(
            [
                float(row.future_return)
                for row in scope
                if row.risk_action == "BLOCK"
                and row.future_return is not None
                and row.future_return > 0
            ]
        ),
        "watch_only_counterfactual_return": _mean(
            [
                float(row.future_return)
                for row in scope
                if row.risk_action == "WATCH_ONLY" and row.future_return is not None
            ]
        ),
    }


def reliability_metrics(
    rows: Iterable[StageObservation],
    *,
    input_count: int,
    checkpoint_reuse_count: int = 0,
    new_business_call_count: int = 0,
    repair_count: int = 0,
    retry_count: int = 0,
    total_tokens: int = 0,
    estimated_cost: float = 0.0,
) -> dict[str, object]:
    values = list(rows)
    successful = sum(row.task_status == "SUCCESS" for row in values)
    schema_errors = sum(row.schema_status not in {"PASS", "SUCCESS"} for row in values)
    search_failed = sum(row.search_status in {"FAILED", "SEARCH_FAILED"} for row in values)
    scored = sum(row.flash_score is not None for row in values)
    actionable = sum(row.actionable for row in values)
    return {
        "input_count": input_count,
        "successful_evaluation_count": successful,
        "schema_error_count": schema_errors,
        "unsupported_claim_count": 0,
        "search_failed_count": search_failed,
        "no_result_count": sum(row.task_status == "NO_RESULT" for row in values),
        "no_material_event_count": sum(row.search_status == "NO_MATERIAL_EVENT" for row in values),
        "repair_count": repair_count,
        "retry_count": retry_count,
        "checkpoint_reuse_count": checkpoint_reuse_count,
        "new_business_call_count": new_business_call_count,
        "selected_count": sum(row.selected for row in values),
        "blocked_count": sum(row.risk_action == "BLOCK" for row in values),
        "watch_only_count": sum(row.risk_action == "WATCH_ONLY" for row in values),
        "late_output_count": sum(not row.actionable for row in values),
        "scoring_coverage_ratio": scored / input_count if input_count else 0.0,
        "actionable_coverage_ratio": actionable / input_count if input_count else 0.0,
        "total_tokens": total_tokens,
        "estimated_cost": estimated_cost,
        "run_status": "COMPLETE" if len(values) == input_count else "PARTIAL",
    }


def compare_v2_v3(
    v2: Mapping[str, object],
    v3: Mapping[str, object],
    *,
    same_cohort: bool,
    same_decision_time: bool,
    same_return_basis: bool,
) -> dict[str, object]:
    if not (same_cohort and same_decision_time and same_return_basis):
        return {
            "status": V2_V3_COMPARISON_CONTRACT_MISMATCH,
            "v3_vs_v2_lift": None,
        }
    return {
        "status": "COMPARABLE",
        "v3_vs_v2_lift": _difference(
            _float_or_none(v3.get("flash_top20_mean_return")),
            _float_or_none(v2.get("flash_top20_mean_return")),
        ),
        "v2_incremental_lift": v2.get("incremental_lift"),
        "v3_incremental_lift": v3.get("incremental_lift"),
    }


def aggregate_daily_metrics(
    rows: Sequence[Mapping[str, object]],
    *,
    metric: str,
    minimum_bootstrap_days: int = 5,
    bootstrap_samples: int = 1000,
    seed: int = 7,
) -> dict[str, object]:
    values = [
        float(row[metric])
        for row in rows
        if row.get(metric) is not None
    ]
    result = {
        "matured_day_count": len(rows),
        "valid_day_count": len(values),
        "mean": _mean(values),
        "median": median(values) if values else None,
        "std": pstdev(values) if len(values) > 1 else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "positive_day_ratio": _ratio(values, lambda value: value > 0),
        "bootstrap_unit": "RANKING_DAY",
    }
    if len(values) < minimum_bootstrap_days:
        result.update({
            "bootstrap_status": "INSUFFICIENT_DAILY_SAMPLES",
            "ci95_low": None,
            "ci95_high": None,
        })
        return result
    rng = random.Random(seed)
    means = [
        fmean(rng.choice(values) for _ in range(len(values)))
        for _ in range(bootstrap_samples)
    ]
    means.sort()
    result.update({
        "bootstrap_status": "CALCULATED",
        "ci95_low": means[max(0, int(bootstrap_samples * 0.025) - 1)],
        "ci95_high": means[min(bootstrap_samples - 1, int(bootstrap_samples * 0.975))],
    })
    return result


def _mean(values: Sequence[float]) -> float | None:
    return fmean(values) if values else None


def _difference(left: float | None, right: float | None) -> float | None:
    return left - right if left is not None and right is not None else None


def _ratio(values: Sequence[float], predicate) -> float | None:
    return sum(predicate(value) for value in values) / len(values) if values else None


def _float_or_none(value: object) -> float | None:
    return float(value) if value is not None else None
