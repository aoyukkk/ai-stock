from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from statistics import fmean
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.ranking_evaluation import (
    RankingEvaluationDailyMetric,
    RankingEvaluationForwardOutcome,
    RankingEvaluationSnapshot,
    RankingEvaluationSnapshotItem,
)
from services.ranking_evaluation.constants import (
    RETURN_BASIS,
)
from services.ranking_evaluation.utils import stable_hash


VALID_OUTCOME_STATUSES = {
    "MATURED",
    "CORPORATE_ACTION_REVIEW",
}


@dataclass(frozen=True)
class MetricObservation:
    stock_code: str
    original_rank: int
    return_decimal: float


def _average_ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            result[indexed[position][0]] = average_rank
        cursor = end
    return result


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def spearman_rank_ic(
    observations: Sequence[MetricObservation], *, top_n: int = 100
) -> float | None:
    """Positive IC means a better original rank is followed by a higher return."""
    if len(observations) < 2:
        return None
    rank_signal = [float(top_n + 1 - row.original_rank) for row in observations]
    returns = [float(row.return_decimal) for row in observations]
    return _pearson(_average_ranks(rank_signal), _average_ranks(returns))


def original_group(original_rank: int) -> str | None:
    if 1 <= original_rank <= 20:
        return "G1"
    if 21 <= original_rank <= 40:
        return "G2"
    if 41 <= original_rank <= 60:
        return "G3"
    if 61 <= original_rank <= 80:
        return "G4"
    if 81 <= original_rank <= 100:
        return "G5"
    return None


def monotonicity(group_returns: dict[str, float | None]) -> dict[str, object]:
    pairs = [("G1", "G2"), ("G2", "G3"), ("G3", "G4"), ("G4", "G5")]
    labels: list[str] = []
    passed = 0
    evaluated = 0
    for left, right in pairs:
        left_value = group_returns.get(left)
        right_value = group_returns.get(right)
        if left_value is None or right_value is None:
            labels.append(f"{left}>{right}:未成熟")
            continue
        evaluated += 1
        is_passed = left_value > right_value
        passed += int(is_passed)
        labels.append(f"{left}>{right}:{'通过' if is_passed else '未通过'}")
    return {
        "passed_count": passed,
        "evaluated_count": evaluated,
        "label": f"{passed}/4" if evaluated == 4 else "未成熟",
        "comparisons": labels,
        "fully_monotonic": evaluated == 4 and passed == 4,
    }


def calculate_metric_payload(
    observations: Iterable[MetricObservation],
    *,
    top_n: int = 100,
    expected_group_size: int = 20,
    minimum_rank_ic_samples: int = 2,
) -> dict[str, object]:
    rows = sorted(observations, key=lambda row: row.original_rank)
    valid_count = len(rows)
    returns = [row.return_decimal for row in rows]
    group_returns: dict[str, float | None] = {}
    group_counts: dict[str, int] = {}
    for group_name in ("G1", "G2", "G3", "G4", "G5"):
        group_values = [
            row.return_decimal
            for row in rows
            if original_group(row.original_rank) == group_name
        ]
        group_counts[group_name] = len(group_values)
        group_returns[group_name] = fmean(group_values) if group_values else None

    top_values = [row.return_decimal for row in rows if 1 <= row.original_rank <= 20]
    bottom_values = [
        row.return_decimal for row in rows if 81 <= row.original_rank <= 100
    ]
    top_average = fmean(top_values) if top_values else None
    bottom_average = fmean(bottom_values) if bottom_values else None
    spread = (
        top_average - bottom_average
        if top_average is not None and bottom_average is not None
        else None
    )
    ic = (
        spearman_rank_ic(rows, top_n=top_n)
        if valid_count >= minimum_rank_ic_samples
        else None
    )
    mono = monotonicity(group_returns)
    return {
        "calculation_status": (
            "NOT_MATURED"
            if valid_count == 0
            else "MATURED"
            if valid_count == top_n
            else "PARTIAL"
        ),
        "valid_sample_count": valid_count,
        "missing_sample_count": max(0, top_n - valid_count),
        "sample_coverage": valid_count / top_n if top_n else 0.0,
        "average_return": fmean(returns) if returns else None,
        "win_rate": (
            sum(value > 0 for value in returns) / valid_count if valid_count else None
        ),
        "rank_ic": ic,
        "top20_average_return": top_average,
        "bottom20_average_return": bottom_average,
        "top20_bottom20_spread": spread,
        "top20_coverage": len(top_values) / expected_group_size,
        "bottom20_coverage": len(bottom_values) / expected_group_size,
        "top20_valid_count": len(top_values),
        "bottom20_valid_count": len(bottom_values),
        "group_returns": group_returns,
        "group_counts": group_counts,
        "group_coverage": {
            name: group_counts[name] / expected_group_size
            for name in ("G1", "G2", "G3", "G4", "G5")
        },
        "monotonic_pass_count": mono["passed_count"],
        "monotonic_evaluated_count": mono["evaluated_count"],
        "monotonicity_label": mono["label"],
        "monotonicity_comparisons": mono["comparisons"],
        "fully_monotonic": mono["fully_monotonic"],
        "adjacent_spreads": {
            f"{left}-{right}": (
                group_returns[left] - group_returns[right]
                if group_returns[left] is not None
                and group_returns[right] is not None
                else None
            )
            for left, right in (
                ("G1", "G2"),
                ("G2", "G3"),
                ("G3", "G4"),
                ("G4", "G5"),
            )
        },
    }


class RankingMetricCalculator:
    def __init__(
        self,
        session: Session,
        *,
        minimum_rank_ic_samples: int = 2,
        config: dict | None = None,
    ):
        self.session = session
        configured = (
            (config or {}).get("metrics", {}).get("minimum_rank_ic_samples")
            if config
            else None
        )
        self.minimum_rank_ic_samples = int(
            configured if configured is not None else minimum_rank_ic_samples
        )

    def calculate_snapshot_horizon(
        self, snapshot_id: int, horizon: int
    ) -> RankingEvaluationDailyMetric:
        snapshot = self.session.get(RankingEvaluationSnapshot, snapshot_id)
        if snapshot is None:
            raise ValueError(f"ranking snapshot not found: {snapshot_id}")

        rows = self.session.execute(
            select(RankingEvaluationSnapshotItem, RankingEvaluationForwardOutcome)
            .join(
                RankingEvaluationForwardOutcome,
                (
                    RankingEvaluationForwardOutcome.snapshot_item_id
                    == RankingEvaluationSnapshotItem.id
                )
                & (RankingEvaluationForwardOutcome.horizon == horizon),
                isouter=True,
            )
            .where(RankingEvaluationSnapshotItem.snapshot_id == snapshot_id)
            .order_by(RankingEvaluationSnapshotItem.original_rank)
        ).all()

        observations: list[MetricObservation] = []
        outcome_fingerprints: list[dict[str, object]] = []
        for item, outcome in rows:
            outcome_fingerprints.append(
                {
                    "stock_code": item.stock_code,
                    "rank": item.original_rank,
                    "row_data_status": item.row_data_status,
                    "outcome_status": outcome.outcome_status if outcome else "ABSENT",
                    "return_decimal": outcome.return_decimal if outcome else None,
                    "source_data_hash": outcome.source_data_hash if outcome else None,
                }
            )
            if (
                item.row_data_status == "NORMAL"
                and outcome is not None
                and outcome.outcome_status in VALID_OUTCOME_STATUSES
                and outcome.return_decimal is not None
            ):
                observations.append(
                    MetricObservation(
                        stock_code=item.stock_code,
                        original_rank=item.original_rank,
                        return_decimal=float(outcome.return_decimal),
                    )
                )

        payload = calculate_metric_payload(
            observations,
            top_n=snapshot.top_n,
            minimum_rank_ic_samples=self.minimum_rank_ic_samples,
        )
        input_hash = stable_hash(
            {
                "snapshot_hash": snapshot.snapshot_hash,
                "horizon": horizon,
                "outcomes": outcome_fingerprints,
            }
        )
        existing = self.session.scalar(
            select(RankingEvaluationDailyMetric).where(
                RankingEvaluationDailyMetric.snapshot_id == snapshot_id,
                RankingEvaluationDailyMetric.horizon == horizon,
            )
        )
        calculated_at = datetime.now(timezone.utc)
        values = {
            "factor_version": snapshot.factor_version,
            "ranking_trade_date": snapshot.ranking_trade_date,
            "horizon": horizon,
            "evaluation_version": snapshot.evaluation_version,
            "return_basis": RETURN_BASIS,
            "calculation_status": payload["calculation_status"],
            "valid_sample_count": payload["valid_sample_count"],
            "missing_sample_count": payload["missing_sample_count"],
            "coverage_ratio": payload["sample_coverage"],
            "rank_ic": payload["rank_ic"],
            "top20_mean_return": payload["top20_average_return"],
            "bottom20_mean_return": payload["bottom20_average_return"],
            "spread": payload["top20_bottom20_spread"],
            "top20_valid_count": payload["top20_valid_count"],
            "bottom20_valid_count": payload["bottom20_valid_count"],
            "top20_coverage_ratio": payload["top20_coverage"],
            "bottom20_coverage_ratio": payload["bottom20_coverage"],
            "group_returns_json": payload["group_returns"],
            "group_valid_counts_json": payload["group_counts"],
            "group_coverage_json": payload["group_coverage"],
            "adjacent_spreads_json": payload["adjacent_spreads"],
            "monotonicity_pass_count": payload["monotonic_pass_count"],
            "monotonicity_label": payload["monotonicity_label"],
            "metric_input_hash": input_hash,
            "calculated_at": calculated_at,
        }
        if existing is None:
            existing = RankingEvaluationDailyMetric(
                snapshot_id=snapshot_id,
                **values,
            )
            self.session.add(existing)
        elif existing.metric_input_hash != input_hash:
            for key, value in values.items():
                setattr(existing, key, value)
        self.session.flush()
        return existing

    def calculate_snapshot(self, snapshot_id: int, horizons: Sequence[int]) -> None:
        for horizon in horizons:
            self.calculate_snapshot_horizon(snapshot_id, int(horizon))

    def rebuild_snapshot(self, snapshot_id: int) -> int:
        from services.ranking_evaluation.constants import DEFAULT_HORIZONS

        self.calculate_snapshot(snapshot_id, DEFAULT_HORIZONS)
        return len(DEFAULT_HORIZONS)
