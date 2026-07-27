from __future__ import annotations

import math
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from statistics import fmean, pstdev
from typing import Any, Iterable

from sqlalchemy import select

from database.models.ranking_evaluation import (
    RankingEvaluationArtifact,
    RankingEvaluationDailyMetric,
    RankingEvaluationDataIssue,
    RankingEvaluationForwardOutcome,
    RankingEvaluationSnapshot,
    RankingEvaluationSnapshotItem,
    RankingEvaluationState,
    RankingEvaluationWeeklyRun,
)
from services.ranking_evaluation.constants import (
    DEFAULT_HORIZONS,
    EVALUATION_VERSION,
    RETURN_BASIS,
    load_config,
)
from services.ranking_evaluation.export_service import RankingEvaluationExportService
from services.ranking_evaluation.trading_calendar_service import RankingTradingCalendarService
from services.ranking_evaluation.utils import stable_hash


def daily_equal_weight(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return fmean(clean) if clean else None


def rank_ic_audit(values: Iterable[float | None]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if value is not None]
    return {
        "mean": fmean(clean) if clean else None,
        "valid_ranking_day_count": len(clean),
        "min": min(clean) if clean else None,
        "max": max(clean) if clean else None,
        "std": pstdev(clean) if len(clean) >= 2 else (0.0 if clean else None),
    }


def weekly_monotonicity(group_returns: dict[str, float | None]) -> dict[str, Any]:
    pairs = (("G1", "G2"), ("G2", "G3"), ("G3", "G4"), ("G4", "G5"))
    spreads: dict[str, float | None] = {}
    passed = 0
    evaluated = 0
    for left, right in pairs:
        left_value = group_returns.get(left)
        right_value = group_returns.get(right)
        value = (
            float(left_value) - float(right_value)
            if left_value is not None and right_value is not None
            else None
        )
        spreads[f"{left}-{right}"] = value
        if value is not None:
            evaluated += 1
            passed += int(value > 0)
    return {
        "adjacent_spreads": spreads,
        "monotonicity_pass_count": passed if evaluated == 4 else None,
        "monotonicity_label": f"{passed}/4" if evaluated == 4 else "未成熟",
    }


class RankingWeeklyReportService:
    def __init__(
        self,
        session,
        *,
        calendar: RankingTradingCalendarService | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.config = config or load_config()
        self.calendar = calendar or RankingTradingCalendarService()

    def run(self, *, week_ending: date, factor_version: str) -> dict[str, Any]:
        if not factor_version:
            raise ValueError("FACTOR_VERSION_REQUIRED")
        if week_ending.weekday() != 4:
            raise ValueError("WEEK_ENDING_MUST_BE_FRIDAY")
        state = self.session.scalar(
            select(RankingEvaluationState).where(
                RankingEvaluationState.evaluation_version == EVALUATION_VERSION
            )
        )
        if state is None:
            raise ValueError("RANKING_EVALUATION_NOT_ACTIVATED")
        activation = state.activation_date
        as_of_trade_date = self.calendar.latest_open_on_or_before(week_ending)
        snapshots = list(
            self.session.scalars(
                select(RankingEvaluationSnapshot)
                .where(
                    RankingEvaluationSnapshot.factor_version == factor_version,
                    RankingEvaluationSnapshot.ranking_trade_date >= activation,
                    RankingEvaluationSnapshot.ranking_trade_date <= week_ending,
                    RankingEvaluationSnapshot.evaluation_version == EVALUATION_VERSION,
                    RankingEvaluationSnapshot.return_basis == RETURN_BASIS,
                )
                .order_by(RankingEvaluationSnapshot.ranking_trade_date)
            )
        )
        snapshot_ids = [row.id for row in snapshots]
        metrics = (
            list(
                self.session.scalars(
                    select(RankingEvaluationDailyMetric)
                    .where(
                        RankingEvaluationDailyMetric.snapshot_id.in_(snapshot_ids),
                        RankingEvaluationDailyMetric.factor_version == factor_version,
                        RankingEvaluationDailyMetric.return_basis == RETURN_BASIS,
                    )
                    .order_by(
                        RankingEvaluationDailyMetric.ranking_trade_date,
                        RankingEvaluationDailyMetric.horizon,
                    )
                )
            )
            if snapshot_ids
            else []
        )
        eligible_metrics: list[RankingEvaluationDailyMetric] = []
        snapshot_by_id = {row.id: row for row in snapshots}
        for metric in metrics:
            snapshot = snapshot_by_id[metric.snapshot_id]
            due_date = self.calendar.horizon_dates(
                snapshot.ranking_trade_date, (int(metric.horizon),)
            )[int(metric.horizon)]
            if as_of_trade_date is not None and due_date <= as_of_trade_date:
                eligible_metrics.append(metric)
        metrics = eligible_metrics
        issues = list(
            self.session.scalars(
                select(RankingEvaluationDataIssue)
                .where(
                    RankingEvaluationDataIssue.affected_version == factor_version,
                    RankingEvaluationDataIssue.affected_date >= activation,
                    RankingEvaluationDataIssue.affected_date <= week_ending,
                )
                .order_by(
                    RankingEvaluationDataIssue.affected_date,
                    RankingEvaluationDataIssue.issue_code,
                )
            )
        )
        input_hash = stable_hash(
            {
                "evaluation_version": EVALUATION_VERSION,
                "factor_version": factor_version,
                "activation_date": activation,
                "week_ending": week_ending,
                "as_of_trade_date": as_of_trade_date,
                "return_basis": RETURN_BASIS,
                "snapshot_hashes": [row.snapshot_hash for row in snapshots],
                "metric_hashes": [row.metric_input_hash for row in metrics],
                "issue_hashes": [row.issue_hash for row in issues],
            }
        )
        existing = self.session.scalar(
            select(RankingEvaluationWeeklyRun).where(
                RankingEvaluationWeeklyRun.factor_version == factor_version,
                RankingEvaluationWeeklyRun.week_ending == week_ending,
                RankingEvaluationWeeklyRun.evaluation_version == EVALUATION_VERSION,
            )
        )
        if existing:
            if existing.input_hash != input_hash:
                raise ValueError("WEEKLY_REPORT_IMMUTABLE_CONFLICT")
            return self._result(existing, "EXISTING_IMMUTABLE_REPORT")

        summary_by_horizon = self._summarize(metrics)
        data_status = _data_status(snapshots, issues)
        generated_at = datetime.now(timezone.utc)
        summary = {
            "factor_version": factor_version,
            "evaluation_version": EVALUATION_VERSION,
            "activation_date": activation,
            "week_ending": week_ending,
            "as_of_trade_date": as_of_trade_date,
            "return_basis": RETURN_BASIS,
            "snapshot_count": len(snapshots),
            "data_status": data_status,
            "horizons": summary_by_horizon,
            "output_excel": bool(self.config.get("output_excel", True)),
            "aggregation_contract": (
                "Compute each ranking day's metric first, then arithmetic-mean "
                "matured daily metrics with equal day weights."
            ),
        }
        summary = json.loads(json.dumps(summary, ensure_ascii=False, default=str))
        report_hash = stable_hash(summary)
        summary["report_hash"] = report_hash
        run_id = f"ranking-weekly-{report_hash[:24]}"
        daily_rows = [_metric_dict(row) for row in metrics]
        details = self._ranking_details(snapshot_ids, as_of_trade_date)
        quality_rows = [_issue_dict(row) for row in issues]
        version_audit = [_snapshot_audit(row, run_id, report_hash) for row in snapshots]
        exporter = RankingEvaluationExportService(
            self.config["weekly_output_root"]
        )
        exported = exporter.export(
            factor_version=factor_version,
            week_ending=week_ending.isoformat(),
            run_id=run_id,
            summary=summary,
            daily_metrics=daily_rows,
            ranking_details=details,
            data_quality=quality_rows,
            version_audit=version_audit,
        )
        status = (
            "COMPLETED"
            if exported.workbook_status in {"CREATED", "DISABLED"}
            else "PARTIAL_SUCCESS"
        )
        row = RankingEvaluationWeeklyRun(
            run_id=run_id,
            factor_version=factor_version,
            week_ending=week_ending,
            evaluation_version=EVALUATION_VERSION,
            activation_date=activation,
            as_of_trade_date=as_of_trade_date,
            return_basis=RETURN_BASIS,
            status=status,
            data_status=data_status,
            summary_json=summary,
            input_hash=input_hash,
            report_hash=report_hash,
            artifact_paths_json=exported.paths,
            generated_at=generated_at,
            completed_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.flush()
        for artifact_type, artifact_path in exported.paths.items():
            self.session.add(
                RankingEvaluationArtifact(
                    artifact_id=f"ranking-artifact-{stable_hash([run_id, artifact_type])[:24]}",
                    weekly_run_id=row.id,
                    artifact_type=artifact_type.upper(),
                    artifact_path=artifact_path,
                    input_hash=input_hash,
                    content_hash=exported.hashes[artifact_type],
                    style_hash=(
                        exported.style_hash if artifact_type == "excel" else None
                    ),
                    immutable=True,
                )
            )
        self.session.commit()
        result = self._result(row, status)
        result["workbook_status"] = exported.workbook_status
        result["workbook_error"] = exported.workbook_error
        return result

    @staticmethod
    def _summarize(metrics: list[RankingEvaluationDailyMetric]) -> dict[str, Any]:
        by_horizon: dict[int, list[RankingEvaluationDailyMetric]] = defaultdict(list)
        for metric in metrics:
            by_horizon[int(metric.horizon)].append(metric)
        result: dict[str, Any] = {}
        for horizon in DEFAULT_HORIZONS:
            rows = [
                row
                for row in by_horizon.get(horizon, [])
                if row.calculation_status in {"MATURED", "PARTIAL"}
            ]
            ic = rank_ic_audit(row.rank_ic for row in rows)
            group_returns = {
                group: daily_equal_weight(
                    (row.group_returns_json or {}).get(group) for row in rows
                )
                for group in ("G1", "G2", "G3", "G4", "G5")
            }
            mono = weekly_monotonicity(group_returns)
            result[f"D{horizon}"] = {
                "matured_ranking_day_count": len(rows),
                "rank_ic": ic,
                "average_spread": daily_equal_weight(row.spread for row in rows),
                "group_returns": group_returns,
                "group_valid_sample_counts": {
                    group: sum(
                        int((row.group_valid_counts_json or {}).get(group, 0))
                        for row in rows
                    )
                    for group in ("G1", "G2", "G3", "G4", "G5")
                },
                "group_average_daily_coverage": {
                    group: daily_equal_weight(
                        (row.group_coverage_json or {}).get(group) for row in rows
                    )
                    for group in ("G1", "G2", "G3", "G4", "G5")
                },
                **mono,
            }
        return result

    def _ranking_details(
        self, snapshot_ids: list[int], as_of_trade_date: date | None
    ) -> list[dict[str, Any]]:
        if not snapshot_ids:
            return []
        rows = self.session.execute(
            select(
                RankingEvaluationSnapshot,
                RankingEvaluationSnapshotItem,
                RankingEvaluationForwardOutcome,
            )
            .join(
                RankingEvaluationSnapshotItem,
                RankingEvaluationSnapshotItem.snapshot_id
                == RankingEvaluationSnapshot.id,
            )
            .join(
                RankingEvaluationForwardOutcome,
                RankingEvaluationForwardOutcome.snapshot_item_id
                == RankingEvaluationSnapshotItem.id,
                isouter=True,
            )
            .where(RankingEvaluationSnapshot.id.in_(snapshot_ids))
            .order_by(
                RankingEvaluationSnapshot.ranking_trade_date,
                RankingEvaluationSnapshotItem.original_rank,
                RankingEvaluationForwardOutcome.horizon,
            )
        ).all()
        keyed: dict[tuple[int, int], dict[str, Any]] = {}
        for snapshot, item, outcome in rows:
            key = (snapshot.id, item.id)
            value = keyed.setdefault(
                key,
                {
                    "ranking_trade_date": snapshot.ranking_trade_date,
                    "stock_code": item.stock_code,
                    "stock_name": item.stock_name,
                    "original_rank": item.original_rank,
                    "quant_score": item.quant_score,
                    "factor_version": snapshot.factor_version,
                    "original_group": item.original_group,
                    "baseline_close": item.baseline_close,
                    "row_data_status": item.row_data_status,
                    "row_issue_code": item.row_issue_code,
                    "row_issue_detail": item.row_issue_detail,
                },
            )
            if outcome is not None:
                prefix = f"D{outcome.horizon}"
                value[f"{prefix}_due_trade_date"] = outcome.due_trade_date
                visible = (
                    as_of_trade_date is not None
                    and outcome.due_trade_date <= as_of_trade_date
                )
                value[f"{prefix}_future_close"] = (
                    outcome.future_close if visible else None
                )
                value[f"{prefix}_return_decimal"] = (
                    outcome.return_decimal if visible else None
                )
                value[f"{prefix}_outcome_status"] = (
                    outcome.outcome_status if visible else "NOT_MATURED"
                )
                value[f"{prefix}_missing_reason"] = (
                    outcome.missing_reason if visible else None
                )
        return list(keyed.values())

    @staticmethod
    def _result(row: RankingEvaluationWeeklyRun, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "run_id": row.run_id,
            "factor_version": row.factor_version,
            "week_ending": row.week_ending,
            "activation_date": row.activation_date,
            "as_of_trade_date": row.as_of_trade_date,
            "return_basis": row.return_basis,
            "data_status": row.data_status,
            "report_hash": row.report_hash,
            "summary": row.summary_json,
            "artifacts": row.artifact_paths_json,
            "shadow_only": True,
            "external_api_calls": 0,
            "llm_calls": 0,
            "orders": 0,
            "scheduler": False,
        }


def _metric_dict(row: RankingEvaluationDailyMetric) -> dict[str, Any]:
    return {
        "ranking_trade_date": row.ranking_trade_date,
        "factor_version": row.factor_version,
        "horizon": row.horizon,
        "return_basis": row.return_basis,
        "calculation_status": row.calculation_status,
        "valid_sample_count": row.valid_sample_count,
        "missing_sample_count": row.missing_sample_count,
        "coverage_ratio": row.coverage_ratio,
        "rank_ic": row.rank_ic,
        "top20_mean_return": row.top20_mean_return,
        "bottom20_mean_return": row.bottom20_mean_return,
        "spread": row.spread,
        "top20_valid_count": row.top20_valid_count,
        "bottom20_valid_count": row.bottom20_valid_count,
        "top20_coverage_ratio": row.top20_coverage_ratio,
        "bottom20_coverage_ratio": row.bottom20_coverage_ratio,
        "group_returns": row.group_returns_json,
        "group_valid_counts": row.group_valid_counts_json,
        "group_coverage": row.group_coverage_json,
        "adjacent_spreads": row.adjacent_spreads_json,
        "monotonicity_pass_count": row.monotonicity_pass_count,
        "monotonicity_label": row.monotonicity_label,
        "metric_input_hash": row.metric_input_hash,
    }


def _issue_dict(row: RankingEvaluationDataIssue) -> dict[str, Any]:
    return {
        "issue_code": row.issue_code,
        "issue_level": row.issue_level,
        "affected_date": row.affected_date,
        "affected_stock": row.affected_stock,
        "affected_version": row.affected_version,
        "detail": row.detail,
        "detected_at": row.detected_at,
        "issue_hash": row.issue_hash,
    }


def _snapshot_audit(
    row: RankingEvaluationSnapshot, report_run_id: str, report_hash: str
) -> dict[str, Any]:
    return {
        "source_quant_run_id": row.source_quant_run_id,
        "snapshot_id": row.snapshot_id,
        "ranking_trade_date": row.ranking_trade_date,
        "factor_version": row.factor_version,
        "score_version": row.score_version,
        "production_or_shadow": row.production_or_shadow,
        "source_input_hash": row.source_input_hash,
        "snapshot_hash": row.snapshot_hash,
        "report_run_id": report_run_id,
        "report_hash": report_hash,
        "generated_at": row.generated_at,
        "return_basis": row.return_basis,
    }


def _data_status(
    snapshots: list[RankingEvaluationSnapshot],
    issues: list[RankingEvaluationDataIssue],
) -> str:
    if any(
        row.overall_data_status == "ABNORMAL" for row in snapshots
    ) or any(row.issue_level == "ABNORMAL" for row in issues):
        return "ABNORMAL"
    if any(
        row.overall_data_status == "WARNING" for row in snapshots
    ) or any(row.issue_level == "WARNING" for row in issues):
        return "WARNING"
    return "NORMAL"
