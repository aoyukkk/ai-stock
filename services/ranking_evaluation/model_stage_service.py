from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import select

from database.models.ranking_evaluation import (
    ModelEffectivenessDailyMetric,
    ModelEffectivenessDataIssue,
    ModelEffectivenessStageItem,
    ModelEffectivenessStageSnapshot,
    RankingEvaluationForwardOutcome,
    RankingEvaluationSnapshot,
    RankingEvaluationSnapshotItem,
)
from database.models.event_overlay import (
    EventScreeningItemRecord,
    EventScreeningRunRecord,
)
from services.ranking_evaluation.model_stage_constants import (
    EVALUATION_VERSION,
    EXECUTION_CONTRACT_VERSION,
    FLASH_QUANT_COHORT_MISMATCH,
    RETURN_BASIS,
    STAGE_SNAPSHOT_IMMUTABLE_CONFLICT,
    STAGE_TYPES,
)
from services.ranking_evaluation.model_stage_metrics import (
    StageObservation,
    actionability_status,
    flash_metrics,
    quant_metrics,
    reliability_metrics,
)
from services.ranking_evaluation.trading_calendar_service import (
    RankingTradingCalendarService,
)
from services.ranking_evaluation.utils import stable_hash


SHANGHAI = ZoneInfo("Asia/Shanghai")


class ModelStageEffectivenessService:
    """Capture immutable model-stage facts and reuse existing ranking outcomes."""

    def __init__(self, session, *, calendar=None) -> None:
        self.session = session
        self.calendar = calendar or RankingTradingCalendarService()

    def capture_stage(
        self,
        *,
        cohort_snapshot: RankingEvaluationSnapshot,
        stage_type: str,
        screening_run_id: str,
        screening_version: str,
        rows: Iterable[Mapping[str, Any]],
        prompt_version: str,
        prompt_hash: str,
        output_schema_version: str,
        checkpoint_contract_version: str,
        decision_as_of_time: datetime,
        output_available_at: datetime | None,
        event_review_version: str | None = None,
        risk_version: str | None = None,
        production_or_shadow: str = "SHADOW",
        source_artifacts: Mapping[str, Any] | None = None,
        reliability: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if stage_type not in STAGE_TYPES:
            raise ValueError("INVALID_STAGE_TYPE")
        if not screening_version:
            raise ValueError("MODEL_VERSION_REQUIRED")
        cohort_items = list(
            self.session.scalars(
                select(RankingEvaluationSnapshotItem)
                .where(RankingEvaluationSnapshotItem.snapshot_id == cohort_snapshot.id)
                .order_by(RankingEvaluationSnapshotItem.original_rank)
            )
        )
        by_code = {item.stock_code: item for item in cohort_items}
        incoming = [dict(row) for row in rows]
        incoming_codes = [str(row["stock_code"]).split(".")[0] for row in incoming]
        duplicate_codes = sorted(
            {code for code in incoming_codes if incoming_codes.count(code) > 1}
        )
        input_members = {
            str(row["stock_code"]).split(".")[0]
            for row in incoming
            if bool(row.get("flash_input_member", True))
        }
        cohort_material = [
            {"stock_code": item.stock_code, "rank": item.original_rank}
            for item in cohort_items
        ]
        quant_hash = stable_hash(cohort_material)
        input_material = [
            {
                "stock_code": str(row["stock_code"]).split(".")[0],
                "quant_rank": row.get("original_quant_rank"),
            }
            for row in incoming
            if bool(row.get("flash_input_member", True))
        ]
        flash_input_hash = stable_hash(sorted(input_material, key=lambda row: row["stock_code"]))
        cohort_match = (
            not duplicate_codes
            and len(cohort_items) == cohort_snapshot.top_n
            and input_members == set(by_code)
            and all(
                by_code.get(str(row["stock_code"]).split(".")[0]) is not None
                and (
                    row.get("original_quant_rank") is None
                    or int(row["original_quant_rank"])
                    == by_code[str(row["stock_code"]).split(".")[0]].original_rank
                )
                for row in incoming
                if bool(row.get("flash_input_member", True))
            )
        )
        next_open = self._next_market_open(cohort_snapshot.ranking_trade_date)
        timing_status = actionability_status(output_available_at, next_open)

        normalized = self._normalize_rows(
            cohort_items=cohort_items,
            incoming=incoming,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            output_schema_version=output_schema_version,
            output_available_at=output_available_at,
            next_market_open_at=next_open,
        )
        content_material = {
            "evaluation_version": EVALUATION_VERSION,
            "cohort_snapshot_id": cohort_snapshot.snapshot_id,
            "stage_type": stage_type,
            "screening_run_id": screening_run_id,
            "screening_version": screening_version,
            "event_review_version": event_review_version,
            "risk_version": risk_version,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "output_schema_version": output_schema_version,
            "checkpoint_contract_version": checkpoint_contract_version,
            "quant_top100_hash": quant_hash,
            "flash_input_hash": flash_input_hash,
            "decision_as_of_time": decision_as_of_time,
            "output_available_at": output_available_at,
            "rows": normalized,
        }
        content_hash = stable_hash(content_material)
        existing = self.session.scalar(
            select(ModelEffectivenessStageSnapshot).where(
                ModelEffectivenessStageSnapshot.screening_run_id == screening_run_id,
                ModelEffectivenessStageSnapshot.stage_type == stage_type,
                ModelEffectivenessStageSnapshot.screening_version == screening_version,
            )
        )
        if existing:
            if existing.content_hash != content_hash:
                raise ValueError(STAGE_SNAPSHOT_IMMUTABLE_CONFLICT)
            return self._snapshot_result(existing, "EXISTING_IMMUTABLE_SNAPSHOT")

        snapshot_id = f"model-stage-{content_hash[:24]}"
        reliability_payload = dict(reliability or {})
        if not reliability_payload:
            reliability_payload = reliability_metrics(
                [_observation(row) for row in normalized],
                input_count=len(input_members),
            )
        row = ModelEffectivenessStageSnapshot(
            stage_snapshot_id=snapshot_id,
            cohort_snapshot_id=cohort_snapshot.id,
            evaluation_version=EVALUATION_VERSION,
            stage_type=stage_type,
            ranking_trade_date=cohort_snapshot.ranking_trade_date,
            source_quant_run_id=cohort_snapshot.source_quant_run_id,
            screening_run_id=screening_run_id,
            quant_factor_version=cohort_snapshot.factor_version,
            screening_version=screening_version,
            event_review_version=event_review_version,
            risk_version=risk_version,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            output_schema_version=output_schema_version,
            checkpoint_contract_version=checkpoint_contract_version,
            production_or_shadow=production_or_shadow,
            return_basis=RETURN_BASIS,
            execution_contract_version=EXECUTION_CONTRACT_VERSION,
            quant_top100_hash=quant_hash,
            flash_input_hash=flash_input_hash,
            flash_input_count=len(input_members),
            decision_as_of_time=decision_as_of_time,
            output_available_at=output_available_at,
            next_market_open_at=next_open,
            actionability_status=timing_status,
            cohort_match=cohort_match,
            run_status="COMPLETE" if len(input_members) == cohort_snapshot.top_n else "PARTIAL",
            reliability_json=reliability_payload,
            content_hash=content_hash,
            source_artifacts_json=dict(source_artifacts or {}),
        )
        self.session.add(row)
        self.session.flush()
        for index, item in enumerate(normalized, start=1):
            cohort_item = by_code.get(item["stock_code"])
            self.session.add(
                ModelEffectivenessStageItem(
                    stage_snapshot_id=row.id,
                    cohort_item_id=cohort_item.id if cohort_item else None,
                    source_row_number=index,
                    **item,
                )
            )
        self.session.flush()
        if not cohort_match:
            self._record_issue(
                row,
                FLASH_QUANT_COHORT_MISMATCH,
                "COMPARISON_BLOCKED",
                (
                    f"cohort={len(by_code)} input={len(input_members)} "
                    f"missing={sorted(set(by_code) - input_members)} "
                    f"extra={sorted(input_members - set(by_code))}"
                ),
            )
        for code in sorted(set(by_code) - input_members):
            self._record_issue(
                row, "FLASH_INPUT_STOCK_MISSING", "ABNORMAL", code, stock_code=code
            )
        for code in duplicate_codes:
            self._record_issue(
                row, "FLASH_DUPLICATE_STOCK", "ABNORMAL", code, stock_code=code
            )
        if stage_type != "QUANT" and sum(bool(item["selected_flag"]) for item in normalized) != 20:
            self._record_issue(
                row,
                "FLASH_SELECTION_COUNT_UNEXPECTED",
                "WARNING",
                f"selected={sum(bool(item['selected_flag']) for item in normalized)}",
            )
        if timing_status != "ACTIONABLE_BEFORE_NEXT_OPEN":
            self._record_issue(row, timing_status, "WARNING", timing_status)
        self.session.commit()
        return self._snapshot_result(row, "CREATED")

    def capture_quant_cohort(
        self, *, trade_date: date, factor_version: str
    ) -> dict[str, Any]:
        cohort = self._cohort(trade_date, factor_version)
        items = list(
            self.session.scalars(
                select(RankingEvaluationSnapshotItem)
                .where(RankingEvaluationSnapshotItem.snapshot_id == cohort.id)
                .order_by(RankingEvaluationSnapshotItem.original_rank)
            )
        )
        rows = [
            {
                "stock_code": item.stock_code,
                "stock_name": item.stock_name,
                "original_quant_rank": item.original_rank,
                "quant_score": item.quant_score,
                "flash_input_member": True,
                "selected_flag": item.original_rank <= 20,
                "schema_status": "NOT_APPLICABLE",
                "task_status": "SUCCESS",
                "data_status": item.row_data_status,
            }
            for item in items
        ]
        return self.capture_stage(
            cohort_snapshot=cohort,
            stage_type="QUANT",
            screening_run_id=f"{cohort.source_quant_run_id}:QUANT",
            screening_version=f"QUANT_STAGE:{factor_version}",
            rows=rows,
            prompt_version="NOT_APPLICABLE",
            prompt_hash="NOT_APPLICABLE",
            output_schema_version="QUANT_RANKING_SNAPSHOT_V1",
            checkpoint_contract_version="NOT_APPLICABLE",
            decision_as_of_time=cohort.generated_at,
            output_available_at=cohort.generated_at,
            production_or_shadow="SHADOW",
            source_artifacts={
                "ranking_snapshot_id": cohort.snapshot_id,
                "ranking_snapshot_hash": cohort.snapshot_hash,
            },
            reliability={
                "input_count": len(items),
                "successful_evaluation_count": len(items),
                "selected_count": min(20, len(items)),
                "scoring_coverage_ratio": len(items) / cohort.top_n,
                "run_status": "COMPLETE" if len(items) == cohort.top_n else "PARTIAL",
            },
        )

    def calculate_daily_metrics(
        self, stage_snapshot_id: int, *, horizon: int
    ) -> list[ModelEffectivenessDailyMetric]:
        stage = self.session.get(ModelEffectivenessStageSnapshot, stage_snapshot_id)
        if stage is None:
            raise ValueError("STAGE_SNAPSHOT_NOT_FOUND")
        rows = self.session.execute(
            select(ModelEffectivenessStageItem, RankingEvaluationForwardOutcome)
            .join(
                RankingEvaluationForwardOutcome,
                (
                    RankingEvaluationForwardOutcome.snapshot_item_id
                    == ModelEffectivenessStageItem.cohort_item_id
                )
                & (RankingEvaluationForwardOutcome.horizon == horizon),
                isouter=True,
            )
            .where(ModelEffectivenessStageItem.stage_snapshot_id == stage.id)
            .order_by(ModelEffectivenessStageItem.source_row_number)
        ).all()
        observations = [
            StageObservation(
                stock_code=item.stock_code,
                quant_rank=int(item.original_quant_rank or 9999),
                quant_score=float(item.quant_score) if item.quant_score is not None else None,
                future_return=(
                    float(outcome.return_decimal)
                    if outcome is not None
                    and outcome.outcome_status in {"MATURED", "CORPORATE_ACTION_REVIEW"}
                    and outcome.return_decimal is not None
                    else None
                ),
                flash_score=float(item.flash_score) if item.flash_score is not None else None,
                flash_rank=item.flash_rank,
                selected=bool(item.selected_flag),
                risk_action=item.risk_action,
                event_score=(
                    float(item.event_opportunity_score)
                    if item.event_opportunity_score is not None
                    else None
                ),
                task_status=item.task_status,
                schema_status=item.schema_status,
                search_status=item.search_status,
                actionable=bool(item.actionable_before_next_open),
            )
            for item, outcome in rows
            if not item.nonstandard_extra
        ]
        results = []
        for actionable_only in (False, True):
            scoped = [
                row for row in observations if not actionable_only or row.actionable
            ]
            if stage.stage_type == "QUANT":
                payload = quant_metrics(scoped)
            elif not stage.cohort_match:
                payload = {
                    "calculation_status": "COMPARISON_BLOCKED",
                    "issue_code": FLASH_QUANT_COHORT_MISMATCH,
                }
            else:
                payload = flash_metrics(
                    scoped,
                    actionable_only=False,
                )
                payload["calculation_status"] = (
                    "NOT_MATURED"
                    if not any(row.future_return is not None for row in scoped)
                    else "MATURED"
                )
            metric_hash = stable_hash(
                {
                    "stage_content_hash": stage.content_hash,
                    "horizon": horizon,
                    "actionable_only": actionable_only,
                    "observations": [row.__dict__ for row in scoped],
                }
            )
            existing = self.session.scalar(
                select(ModelEffectivenessDailyMetric).where(
                    ModelEffectivenessDailyMetric.stage_snapshot_id == stage.id,
                    ModelEffectivenessDailyMetric.horizon == horizon,
                    ModelEffectivenessDailyMetric.return_basis == RETURN_BASIS,
                    ModelEffectivenessDailyMetric.actionable_only == actionable_only,
                )
            )
            values = {
                "evaluation_version": EVALUATION_VERSION,
                "stage_type": stage.stage_type,
                "quant_factor_version": stage.quant_factor_version,
                "screening_version": stage.screening_version,
                "ranking_trade_date": stage.ranking_trade_date,
                "horizon": horizon,
                "return_basis": RETURN_BASIS,
                "actionable_only": actionable_only,
                "calculation_status": str(payload.get("calculation_status") or "MATURED"),
                "valid_sample_count": sum(row.future_return is not None for row in scoped),
                "metric_payload_json": json.loads(
                    json.dumps(payload, default=str, ensure_ascii=False)
                ),
                "metric_input_hash": metric_hash,
                "calculated_at": datetime.now(timezone.utc),
            }
            if existing is None:
                existing = ModelEffectivenessDailyMetric(
                    stage_snapshot_id=stage.id, **values
                )
                self.session.add(existing)
            elif existing.metric_input_hash != metric_hash:
                for key, value in values.items():
                    setattr(existing, key, value)
            results.append(existing)
        self.session.commit()
        return results

    def capture_v2_checkpoint(
        self,
        *,
        trade_date: date,
        checkpoint_path: Path,
        audit_path: Path | None = None,
    ) -> dict[str, Any]:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        cohort = self._cohort(trade_date, "TUSHARE_QUANT_V2_CORRECTED_SHADOW")
        audit = {}
        if audit_path and audit_path.exists():
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        selected = set(((audit.get("flash") or {}).get("top20_codes") or []))
        flash = dict(checkpoint.get("flash") or {})
        rows = []
        output_times = []
        audit_by_code = {
            str(item.get("stock_code", "")).split(".")[0]: item
            for item in ((audit.get("flash") or {}).get("audit") or [])
        }
        for code, item in flash.items():
            contract = dict(item.get("checkpoint_contract") or {})
            audit_row = audit_by_code.get(str(code).split(".")[0], {})
            created_at = _parse_datetime(audit_row.get("created_at"))
            if created_at:
                output_times.append(created_at)
            rows.append(
                {
                    "stock_code": str(code).split(".")[0],
                    "stock_name": item.get("stock_name") or str(code),
                    "original_quant_rank": item.get("quant_rank"),
                    "quant_score": item.get("quant_score"),
                    "flash_input_member": True,
                    "flash_score": item.get("llm_score"),
                    "selected_flag": str(code).split(".")[0] in selected,
                    "screening_action": item.get("screening_decision"),
                    "recommendation": item.get("screening_decision"),
                    "confidence": item.get("confidence"),
                    "risk_action": item.get("screening_decision"),
                    "reason": item.get("reason"),
                    "schema_status": audit_row.get("schema_status") or "PASS",
                    "task_status": (
                        "SUCCESS"
                        if item.get("execution_status") == "SUCCESS"
                        else item.get("execution_status") or "FAILED"
                    ),
                    "attempt_count": 1,
                    "repair_count": int(
                        bool((audit_row.get("diagnostics") or {}).get("repair_attempted"))
                    ),
                    "checkpoint_reused": False,
                    "model_provider": "DEEPSEEK",
                    "model_alias": audit_row.get("model_alias"),
                    "resolved_model_name": audit_row.get("actual_model"),
                    "input_hash": contract.get("input_hash"),
                    "context_hash": item.get("context_hash"),
                    "checkpoint_contract_hash": item.get("checkpoint_contract_hash"),
                    "data_status": "NORMAL",
                    "raw_payload_json": item,
                }
            )
        first = next(iter(flash.values()), {})
        contract = dict(first.get("checkpoint_contract") or {})
        decision = _parse_datetime(contract.get("decision_as_of_time")) or datetime.combine(
            trade_date, time(17, 0), SHANGHAI
        )
        return self.capture_stage(
            cohort_snapshot=cohort,
            stage_type="FLASH_V2",
            screening_run_id=str(checkpoint.get("base_run_id") or checkpoint_path.parent.name),
            screening_version="FLASH_V2_STRUCTURED_LIGHT_SCREENING_V5",
            rows=rows,
            prompt_version=str(first.get("prompt_version") or "UNKNOWN"),
            prompt_hash=str(contract.get("prompt_hash") or "UNKNOWN"),
            output_schema_version=str(first.get("flash_score_version") or "UNKNOWN"),
            checkpoint_contract_version=str(
                contract.get("checkpoint_contract_version") or "UNKNOWN"
            ),
            decision_as_of_time=decision,
            output_available_at=max(output_times) if output_times else None,
            risk_version=contract.get("risk_version"),
            source_artifacts={
                "checkpoint_path": str(checkpoint_path.resolve()),
                "checkpoint_hash": _file_hash(checkpoint_path),
                "audit_path": str(audit_path.resolve()) if audit_path else None,
            },
            reliability={
                "input_count": len(flash),
                "successful_evaluation_count": sum(
                    item.get("execution_status") == "SUCCESS" for item in flash.values()
                ),
                "schema_error_count": sum(
                    (row.get("schema_status") or "PASS") != "PASS"
                    for row in audit_by_code.values()
                ),
                "checkpoint_reuse_count": 0,
                "new_business_call_count": int(
                    (audit.get("flash") or {}).get("new_business_calls") or 0
                ),
                "selected_count": len(selected),
                "scoring_coverage_ratio": len(flash) / cohort.top_n,
                "run_status": "PARTIAL" if len(flash) != cohort.top_n else "COMPLETE",
            },
        )

    def capture_v3_run(self, *, screening_run_id: str) -> dict[str, Any]:
        run = self.session.scalar(
            select(EventScreeningRunRecord).where(
                EventScreeningRunRecord.run_id == screening_run_id
            )
        )
        if run is None:
            raise ValueError("EVENT_SCREENING_RUN_NOT_FOUND")
        cohort = self._cohort(run.trade_date, run.factor_version)
        items = list(
            self.session.scalars(
                select(EventScreeningItemRecord)
                .where(EventScreeningItemRecord.screening_run_id == run.id)
                .order_by(EventScreeningItemRecord.v3_rank)
            )
        )
        manifest = dict(run.manifest_json or {})
        rows = [
            {
                "stock_code": item.stock_code,
                "stock_name": item.stock_name,
                "original_quant_rank": item.quant_rank,
                "quant_score": item.quant_score,
                "flash_input_member": True,
                "flash_score": item.v3_screening_score,
                "flash_rank": item.v3_rank,
                "selected_flag": item.selected_top20,
                "screening_action": item.risk_action,
                "recommendation": item.risk_action,
                "risk_action": item.risk_action,
                "schema_status": "PASS",
                "task_status": "SUCCESS",
                "attempt_count": 1,
                "checkpoint_reused": item.checkpoint_status == "REUSED",
                "model_provider": "DEEPSEEK",
                "model_alias": "event-overlay-v3-flash",
                "resolved_model_name": "deepseek-v4-flash",
                "event_opportunity_score": item.event_opportunity_score,
                "event_evidence_confidence": item.evidence_confidence,
                "event_action": item.risk_action,
                "direct_search_used": manifest.get("real_search_enabled", False),
                "evidence_source": (
                    "FLASH_DIRECT_SEARCH"
                    if manifest.get("real_search_enabled")
                    else "FROZEN_EVENT_EVIDENCE"
                ),
                "search_status": item.search_status,
                "event_evidence_snapshot_hash": item.event_snapshot_id,
                "data_status": "NORMAL",
                "raw_payload_json": dict(item.raw_quant_json or {}),
            }
            for item in items
        ]
        output_available = _parse_datetime(manifest.get("generated_at"))
        return self.capture_stage(
            cohort_snapshot=cohort,
            stage_type="FLASH_V3_EVENT_OVERLAY",
            screening_run_id=run.run_id,
            screening_version=run.screening_version,
            rows=rows,
            prompt_version=str(manifest.get("prompt_version") or "UNKNOWN"),
            prompt_hash=str(manifest.get("prompt_hash") or "UNKNOWN"),
            output_schema_version=str(manifest.get("event_review_version") or "UNKNOWN"),
            checkpoint_contract_version=str(
                manifest.get("search_contract_version") or "UNKNOWN"
            ),
            decision_as_of_time=run.decision_as_of_time,
            output_available_at=output_available,
            event_review_version=manifest.get("event_review_version"),
            risk_version=manifest.get("risk_version"),
            source_artifacts={
                "event_screening_content_hash": run.content_hash,
                "manifest": manifest,
            },
            reliability={
                "input_count": run.input_count,
                "successful_evaluation_count": run.output_count,
                "search_failed_count": sum(
                    item.search_status in {"FAILED", "SEARCH_FAILED"} for item in items
                ),
                "checkpoint_reuse_count": run.reused_checkpoint_count,
                "new_business_call_count": run.actual_network_calls,
                "selected_count": sum(item.selected_top20 for item in items),
                "scoring_coverage_ratio": run.output_count / cohort.top_n,
                "run_status": "COMPLETE" if run.input_count == cohort.top_n else "PARTIAL",
            },
        )

    def _cohort(self, trade_date: date, factor_version: str) -> RankingEvaluationSnapshot:
        rows = list(
            self.session.scalars(
                select(RankingEvaluationSnapshot).where(
                    RankingEvaluationSnapshot.ranking_trade_date == trade_date,
                    RankingEvaluationSnapshot.factor_version == factor_version,
                )
            )
        )
        if not rows:
            raise ValueError("QUANT_COHORT_NOT_FOUND")
        if len(rows) > 1:
            raise ValueError("MODEL_VERSION_REQUIRED")
        return rows[0]

    def _next_market_open(self, trade_date: date) -> datetime | None:
        try:
            next_date = self.calendar.horizon_dates(trade_date, (1,))[1]
        except ValueError:
            return None
        return datetime.combine(next_date, time(9, 30), SHANGHAI)

    @staticmethod
    def _normalize_rows(
        *,
        cohort_items,
        incoming,
        prompt_version,
        prompt_hash,
        output_schema_version,
        output_available_at,
        next_market_open_at,
    ) -> list[dict[str, Any]]:
        by_code = {str(row["stock_code"]).split(".")[0]: row for row in incoming}
        normalized = []
        selected_rows = sorted(
            [row for row in incoming if bool(row.get("selected_flag"))],
            key=lambda row: (
                row.get("final_selected_rank") or 9999,
                -(float(row.get("flash_score") or -1e9)),
            ),
        )
        selected_rank = {
            str(row["stock_code"]).split(".")[0]: index
            for index, row in enumerate(selected_rows, start=1)
        }
        ranked_rows = sorted(
            [row for row in incoming if row.get("flash_score") is not None],
            key=lambda row: -float(row["flash_score"]),
        )
        flash_rank = {
            str(row["stock_code"]).split(".")[0]: index
            for index, row in enumerate(ranked_rows, start=1)
        }
        ordered_codes = [item.stock_code for item in cohort_items]
        extras = [
            str(row["stock_code"]).split(".")[0]
            for row in incoming
            if str(row["stock_code"]).split(".")[0] not in set(ordered_codes)
        ]
        for code in ordered_codes + extras:
            source = by_code.get(code)
            cohort = next((item for item in cohort_items if item.stock_code == code), None)
            if source is None:
                source = {
                    "stock_code": code,
                    "stock_name": cohort.stock_name if cohort else code,
                    "original_quant_rank": cohort.original_rank if cohort else None,
                    "quant_score": cohort.quant_score if cohort else None,
                    "flash_input_member": False,
                    "schema_status": "NOT_RUN",
                    "task_status": "MISSING_FROM_INPUT",
                    "data_status": "ABNORMAL",
                }
            status = actionability_status(output_available_at, next_market_open_at)
            normalized.append(
                {
                    "stock_code": code,
                    "stock_name": str(source.get("stock_name") or code),
                    "original_quant_rank": source.get("original_quant_rank")
                    if source.get("original_quant_rank") is not None
                    else (cohort.original_rank if cohort else None),
                    "quant_score": source.get("quant_score")
                    if source.get("quant_score") is not None
                    else (cohort.quant_score if cohort else None),
                    "flash_input_member": bool(source.get("flash_input_member", True)),
                    "nonstandard_extra": cohort is None,
                    "flash_score": source.get("flash_score"),
                    "flash_rank": source.get("flash_rank") or flash_rank.get(code),
                    "selected_flag": bool(source.get("selected_flag", False)),
                    "final_selected_rank": source.get("final_selected_rank")
                    or selected_rank.get(code),
                    "screening_action": source.get("screening_action"),
                    "recommendation": source.get("recommendation"),
                    "confidence": source.get("confidence"),
                    "risk_level": source.get("risk_level"),
                    "risk_action": source.get("risk_action"),
                    "reason": source.get("reason"),
                    "schema_status": str(source.get("schema_status") or "UNKNOWN"),
                    "task_status": str(source.get("task_status") or "UNKNOWN"),
                    "attempt_count": int(source.get("attempt_count") or 0),
                    "repair_count": int(source.get("repair_count") or 0),
                    "checkpoint_reused": bool(source.get("checkpoint_reused", False)),
                    "model_provider": source.get("model_provider"),
                    "model_alias": source.get("model_alias"),
                    "resolved_model_name": source.get("resolved_model_name"),
                    "prompt_version": prompt_version,
                    "prompt_hash": prompt_hash,
                    "output_schema_version": output_schema_version,
                    "input_hash": source.get("input_hash"),
                    "context_hash": source.get("context_hash"),
                    "checkpoint_contract_hash": source.get("checkpoint_contract_hash"),
                    "output_available_at": output_available_at,
                    "next_market_open_at": next_market_open_at,
                    "actionable_before_next_open": status == "ACTIONABLE_BEFORE_NEXT_OPEN",
                    "event_opportunity_score": source.get("event_opportunity_score"),
                    "event_evidence_confidence": source.get("event_evidence_confidence"),
                    "event_action": source.get("event_action"),
                    "direct_search_used": source.get("direct_search_used"),
                    "evidence_source": source.get("evidence_source"),
                    "search_status": source.get("search_status"),
                    "event_evidence_snapshot_hash": source.get("event_evidence_snapshot_hash"),
                    "data_status": (
                        "WARNING"
                        if cohort is None
                        else str(source.get("data_status") or "NORMAL")
                    ),
                    "raw_payload_json": json.loads(
                        json.dumps(
                            dict(source.get("raw_payload_json") or source),
                            ensure_ascii=False,
                            default=str,
                        )
                    ),
                }
            )
        return normalized

    def _record_issue(
        self,
        stage,
        code,
        level,
        detail,
        *,
        stock_code=None,
    ) -> None:
        material = {
            "stage_snapshot_id": stage.stage_snapshot_id,
            "issue_code": code,
            "stock_code": stock_code,
            "detail": detail,
        }
        issue_hash = stable_hash(material)
        if self.session.scalar(
            select(ModelEffectivenessDataIssue).where(
                ModelEffectivenessDataIssue.issue_hash == issue_hash
            )
        ):
            return
        self.session.add(
            ModelEffectivenessDataIssue(
                issue_id=f"model-issue-{issue_hash[:24]}",
                issue_hash=issue_hash,
                stage_snapshot_id=stage.id,
                issue_code=code,
                issue_level=level,
                affected_date=stage.ranking_trade_date,
                affected_stock=stock_code,
                quant_factor_version=stage.quant_factor_version,
                screening_version=stage.screening_version,
                detail=detail,
                detected_at=datetime.now(timezone.utc),
            )
        )

    @staticmethod
    def _snapshot_result(row, status) -> dict[str, Any]:
        return {
            "status": status,
            "stage_snapshot_id": row.stage_snapshot_id,
            "stage_type": row.stage_type,
            "screening_run_id": row.screening_run_id,
            "screening_version": row.screening_version,
            "cohort_match": row.cohort_match,
            "actionability_status": row.actionability_status,
            "content_hash": row.content_hash,
            "shadow_only": True,
            "llm_calls": 0,
            "external_search_calls": 0,
            "orders": 0,
            "scheduler": False,
        }


def _observation(row: Mapping[str, Any]) -> StageObservation:
    return StageObservation(
        stock_code=str(row["stock_code"]),
        quant_rank=int(row.get("original_quant_rank") or 9999),
        quant_score=float(row["quant_score"]) if row.get("quant_score") is not None else None,
        future_return=None,
        flash_score=float(row["flash_score"]) if row.get("flash_score") is not None else None,
        flash_rank=row.get("flash_rank"),
        selected=bool(row.get("selected_flag")),
        risk_action=row.get("risk_action"),
        event_score=(
            float(row["event_opportunity_score"])
            if row.get("event_opportunity_score") is not None
            else None
        ),
        task_status=str(row.get("task_status") or "UNKNOWN"),
        schema_status=str(row.get("schema_status") or "UNKNOWN"),
        search_status=row.get("search_status"),
        actionable=bool(row.get("actionable_before_next_open")),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI)


def _file_hash(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
