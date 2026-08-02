from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import or_, select

from database.models.ranking_evaluation import (
    ModelEffectivenessDailyMetric,
    ModelEffectivenessDataIssue,
    ModelEffectivenessStageItem,
    ModelEffectivenessStageSnapshot,
    ModelEffectivenessWeeklyRun,
    RankingEvaluationForwardOutcome,
)
from services.ranking_evaluation.model_stage_constants import (
    EVALUATION_VERSION,
    EXECUTION_CONTRACT_VERSION,
    RETURN_BASIS,
)
from services.ranking_evaluation.model_stage_export_service import (
    ModelStageExportService,
)
from services.ranking_evaluation.model_stage_metrics import aggregate_daily_metrics
from services.ranking_evaluation.utils import stable_hash


class ModelStageWeeklyService:
    def __init__(self, session) -> None:
        self.session = session

    def run(
        self,
        *,
        week_ending: date,
        quant_factor_version: str,
        screening_version: str,
        output_excel: bool = True,
    ) -> dict[str, Any]:
        stages = list(
            self.session.scalars(
                select(ModelEffectivenessStageSnapshot)
                .where(
                    ModelEffectivenessStageSnapshot.ranking_trade_date <= week_ending,
                    ModelEffectivenessStageSnapshot.quant_factor_version
                    == quant_factor_version,
                    or_(
                        ModelEffectivenessStageSnapshot.stage_type == "QUANT",
                        ModelEffectivenessStageSnapshot.screening_version
                        == screening_version,
                    ),
                )
                .order_by(ModelEffectivenessStageSnapshot.ranking_trade_date)
            )
        )
        if not stages:
            raise ValueError("MODEL_STAGE_SNAPSHOT_NOT_FOUND")
        stage_ids = [row.id for row in stages]
        metrics = list(
            self.session.scalars(
                select(ModelEffectivenessDailyMetric)
                .where(ModelEffectivenessDailyMetric.stage_snapshot_id.in_(stage_ids))
                .order_by(
                    ModelEffectivenessDailyMetric.ranking_trade_date,
                    ModelEffectivenessDailyMetric.horizon,
                    ModelEffectivenessDailyMetric.actionable_only,
                )
            )
        )
        issues = list(
            self.session.scalars(
                select(ModelEffectivenessDataIssue)
                .where(ModelEffectivenessDataIssue.stage_snapshot_id.in_(stage_ids))
                .order_by(ModelEffectivenessDataIssue.affected_date)
            )
        )
        daily = [_metric_row(row) for row in metrics]
        summary_metrics: dict[str, Any] = {}
        for horizon in (1, 3, 5, 10):
            for actionable in (False, True):
                scoped = [
                    row.metric_payload_json
                    for row in metrics
                    if row.horizon == horizon
                    and row.actionable_only == actionable
                    and row.calculation_status != "NOT_MATURED"
                ]
                key = f"D{horizon}_{'ACTIONABLE' if actionable else 'RESEARCH'}"
                summary_metrics[key] = {
                    metric: aggregate_daily_metrics(scoped, metric=metric)
                    for metric in (
                        "rank_ic",
                        "quant_score_ic",
                        "flash_score_ic",
                        "selection_spread",
                        "incremental_lift",
                        "promote_demote_spread",
                    )
                }
        version_audit = [
            {
                "stage_snapshot_id": row.stage_snapshot_id,
                "ranking_trade_date": row.ranking_trade_date,
                "stage_type": row.stage_type,
                "quant_factor_version": row.quant_factor_version,
                "screening_version": row.screening_version,
                "event_review_version": row.event_review_version,
                "risk_version": row.risk_version,
                "prompt_version": row.prompt_version,
                "prompt_hash": row.prompt_hash,
                "output_schema_version": row.output_schema_version,
                "checkpoint_contract_version": row.checkpoint_contract_version,
                "return_basis": row.return_basis,
                "execution_contract_version": row.execution_contract_version,
                "quant_top100_hash": row.quant_top100_hash,
                "flash_input_hash": row.flash_input_hash,
                "content_hash": row.content_hash,
                "cohort_match": row.cohort_match,
                "actionability_status": row.actionability_status,
            }
            for row in stages
        ]
        input_hash = stable_hash(
            {
                "stages": [row.content_hash for row in stages],
                "metrics": [row.metric_input_hash for row in metrics],
                "issues": [row.issue_hash for row in issues],
            }
        )
        summary = {
            "evaluation_version": EVALUATION_VERSION,
            "week_ending": week_ending,
            "quant_factor_version": quant_factor_version,
            "screening_version": screening_version,
            "return_basis": RETURN_BASIS,
            "execution_contract_version": EXECUTION_CONTRACT_VERSION,
            "stage_snapshot_count": len(stages),
            "data_status": (
                "COMPARISON_BLOCKED"
                if any(not row.cohort_match for row in stages)
                else "WARNING"
                if issues
                else "NORMAL"
            ),
            "evidence_status": "NOT_MATURED" if not metrics else "INSUFFICIENT_DATA",
            "metrics": summary_metrics,
            "versions": {
                "quant_factor_versions": sorted({row.quant_factor_version for row in stages}),
                "screening_versions": sorted({row.screening_version for row in stages}),
                "prompt_versions": sorted({row.prompt_version for row in stages}),
                "prompt_hashes": sorted({row.prompt_hash for row in stages}),
                "schema_versions": sorted({row.output_schema_version for row in stages}),
                "checkpoint_contract_versions": sorted(
                    {row.checkpoint_contract_version for row in stages}
                ),
            },
            "aggregation_contract": "DAILY_FIRST_THEN_EQUAL_WEIGHT_DAYS",
            "bootstrap_unit": "RANKING_DAY",
            "input_hash": input_hash,
        }
        report_hash = stable_hash(summary)
        summary["report_hash"] = report_hash
        summary = json.loads(json.dumps(summary, default=str, ensure_ascii=False))
        run_id = f"model-effectiveness-{report_hash[:24]}"
        existing = self.session.scalar(
            select(ModelEffectivenessWeeklyRun).where(
                ModelEffectivenessWeeklyRun.run_id == run_id
            )
        )
        if existing:
            return self._result(existing, "EXISTING_IMMUTABLE_RUN")
        details, promote_demote = self._details(stage_ids)
        reliability = [
            {
                "stage_snapshot_id": row.stage_snapshot_id,
                "ranking_trade_date": row.ranking_trade_date,
                "stage_type": row.stage_type,
                "screening_version": row.screening_version,
                **(row.reliability_json or {}),
            }
            for row in stages
        ]
        quality = [
            {
                "issue_code": row.issue_code,
                "issue_level": row.issue_level,
                "affected_date": row.affected_date,
                "affected_stock": row.affected_stock,
                "quant_factor_version": row.quant_factor_version,
                "screening_version": row.screening_version,
                "detail": row.detail,
            }
            for row in issues
        ]
        exported = ModelStageExportService().export(
            week_ending=week_ending.isoformat(),
            run_id=run_id,
            quant_factor_version=quant_factor_version,
            screening_version=screening_version,
            summary=json.loads(json.dumps(summary, default=str)),
            daily_metrics=daily,
            stage_details=details,
            promote_demote=promote_demote,
            reliability=reliability,
            data_quality=quality,
            version_audit=version_audit,
            output_excel=output_excel,
        )
        status = "COMPLETED" if exported.workbook_status in {"CREATED", "DISABLED"} else "PARTIAL_SUCCESS"
        row = ModelEffectivenessWeeklyRun(
            run_id=run_id,
            evaluation_version=EVALUATION_VERSION,
            quant_factor_version=quant_factor_version,
            screening_version=screening_version,
            week_ending=week_ending,
            return_basis=RETURN_BASIS,
            status=status,
            data_status=summary["data_status"],
            summary_json=summary,
            input_hash=input_hash,
            report_hash=report_hash,
            artifact_paths_json=exported.paths,
            generated_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.commit()
        result = self._result(row, status)
        result["workbook_status"] = exported.workbook_status
        result["workbook_error"] = exported.workbook_error
        return result

    def _details(self, stage_ids: list[int]) -> tuple[list[dict], list[dict]]:
        rows = self.session.execute(
            select(
                ModelEffectivenessStageSnapshot,
                ModelEffectivenessStageItem,
                RankingEvaluationForwardOutcome,
            )
            .join(
                ModelEffectivenessStageItem,
                ModelEffectivenessStageItem.stage_snapshot_id
                == ModelEffectivenessStageSnapshot.id,
            )
            .join(
                RankingEvaluationForwardOutcome,
                RankingEvaluationForwardOutcome.snapshot_item_id
                == ModelEffectivenessStageItem.cohort_item_id,
                isouter=True,
            )
            .where(ModelEffectivenessStageSnapshot.id.in_(stage_ids))
            .order_by(
                ModelEffectivenessStageSnapshot.ranking_trade_date,
                ModelEffectivenessStageItem.source_row_number,
                RankingEvaluationForwardOutcome.horizon,
            )
        ).all()
        details = []
        replacements = []
        for stage, item, outcome in rows:
            role = (
                "PROMOTED"
                if item.selected_flag and (item.original_quant_rank or 999) > 20
                else "DEMOTED"
                if not item.selected_flag and (item.original_quant_rank or 999) <= 20
                else "OVERLAP"
                if item.selected_flag and (item.original_quant_rank or 999) <= 20
                else "UNSELECTED"
            )
            value = {
                "ranking_trade_date": stage.ranking_trade_date,
                "stage_type": stage.stage_type,
                "screening_version": stage.screening_version,
                "stock_code": item.stock_code,
                "stock_name": item.stock_name,
                "quant_rank": item.original_quant_rank,
                "quant_score": item.quant_score,
                "flash_score": item.flash_score,
                "flash_rank": item.flash_rank,
                "selected_flag": item.selected_flag,
                "event_score": item.event_opportunity_score,
                "event_action": item.event_action,
                "risk_action": item.risk_action,
                "horizon": outcome.horizon if outcome else None,
                "due_trade_date": outcome.due_trade_date if outcome else None,
                "baseline_close": outcome.baseline_close if outcome else None,
                "future_close": outcome.future_close if outcome else None,
                "future_return": outcome.return_decimal if outcome else None,
                "outcome_status": outcome.outcome_status if outcome else "NOT_MATURED",
                "actionability": stage.actionability_status,
                "data_status": item.data_status,
                "replacement_role": role,
            }
            details.append(value)
            if role in {"PROMOTED", "DEMOTED"}:
                replacements.append(value)
        return details, replacements

    @staticmethod
    def _result(row, status):
        return {
            "status": status,
            "run_id": row.run_id,
            "week_ending": row.week_ending,
            "quant_factor_version": row.quant_factor_version,
            "screening_version": row.screening_version,
            "data_status": row.data_status,
            "report_hash": row.report_hash,
            "artifacts": row.artifact_paths_json,
            "shadow_only": True,
            "llm_calls": 0,
            "external_search_calls": 0,
            "orders": 0,
            "scheduler": False,
        }


def _metric_row(row: ModelEffectivenessDailyMetric) -> dict[str, Any]:
    return {
        "ranking_trade_date": row.ranking_trade_date,
        "stage_type": row.stage_type,
        "quant_factor_version": row.quant_factor_version,
        "screening_version": row.screening_version,
        "horizon": row.horizon,
        "return_basis": row.return_basis,
        "actionable_only": row.actionable_only,
        "calculation_status": row.calculation_status,
        "valid_sample_count": row.valid_sample_count,
        **(row.metric_payload_json or {}),
    }
