from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from database.models.quant_run import QuantRankResult, QuantRun
from database.models.temporal import RunDataManifestRecord
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationFailureAudit,
    ModelValidationLLMAudit,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
    ValidationAccountSnapshot,
)
from database.session import get_session
from model_validation.service import (
    GuardedValidationService,
    NON_ACTIONABLE_NOTICE,
    _normalize_fundamental,
    _quant_scores,
)
from quant.run_repository import QuantRunRepository
from research.knowledge_mode import LLMKnowledgeMode, validate_knowledge_mode
from research.input_quality import summarize_structured_input
from research.flash_v4 import FLASH_RANKING_VERSION, FLASH_SCORE_VERSION, assert_flash_batch_quality
from research.structured_validation import (
    FUNDAMENTAL_PROMPT_VERSION,
    MODEL_ALIAS,
    SCREENING_PROMPT_VERSION,
    StructuredOutputValidationError,
    StructuredValidationProvider,
)
from stock_codes import display_stock_code, normalize_ts_code


@dataclass(frozen=True)
class ManualSelection:
    stock_code: str
    reason: str = ""
    priority: str = ""


class TraderDemoService:
    """Resumable, per-stock validation batch used only by the trader Excel demo."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.guard = GuardedValidationService(session)
        self._ensure_audit_columns()

    def load_context(
        self,
        quant_run_id: str | None,
        knowledge_mode: LLMKnowledgeMode = LLMKnowledgeMode.STRUCTURED_INPUT_ONLY,
    ) -> tuple[QuantRun, RunDataManifestRecord, list[QuantRankResult]]:
        repo = QuantRunRepository(self.session)
        run = self.session.scalar(select(QuantRun).where(QuantRun.run_id == quant_run_id)) if quant_run_id else repo.latest_actionable()
        if run is None:
            raise ValueError("ACTIONABLE_QUANT_RUN_REQUIRED")
        validate_knowledge_mode(run.run_mode, knowledge_mode)
        manifest = self.session.scalar(select(RunDataManifestRecord).where(RunDataManifestRecord.manifest_id == run.data_manifest_id))
        if manifest is None:
            raise ValueError("RUN_DATA_MANIFEST_REQUIRED")
        rows = list(self.session.scalars(
            select(QuantRankResult).where(QuantRankResult.quant_run_id == run.run_id).order_by(QuantRankResult.rank)
        ))
        if len(rows) != run.scored_count:
            raise ValueError(f"QUANT_RANK_RESULT_INCOMPLETE:{len(rows)}/{run.scored_count}")
        if [row.rank for row in rows] != list(range(1, run.scored_count + 1)):
            raise ValueError("QUANT_RANK_SEQUENCE_INVALID")
        return run, manifest, rows

    @staticmethod
    def evaluation_rows(
        quant_rows: list[QuantRankResult], *, ranks: Iterable[int] | None,
        top_n: int | None, manual: list[ManualSelection],
    ) -> list[QuantRankResult]:
        by_rank = {row.rank: row for row in quant_rows}
        by_code = {_canonical(row.stock_code): row for row in quant_rows}
        chosen: dict[str, QuantRankResult] = {}
        if ranks:
            for rank in ranks:
                if rank not in by_rank:
                    raise ValueError(f"LLM_RANK_NOT_IN_QUANT_RUN:{rank}")
                chosen[_canonical(by_rank[rank].stock_code)] = by_rank[rank]
        elif top_n:
            if top_n <= 0 or top_n > len(quant_rows):
                raise ValueError("INVALID_LLM_TOP_N")
            for row in quant_rows[:top_n]:
                chosen[_canonical(row.stock_code)] = row
        for item in manual:
            code = _canonical(item.stock_code)
            if code not in by_code:
                raise ValueError(f"MANUAL_STOCK_NOT_IN_QUANT_RUN:{item.stock_code}")
            chosen[code] = by_code[code]
        return sorted(chosen.values(), key=lambda row: row.rank)

    def preview(
        self, *, quant_run_id: str | None, ranks: Iterable[int] | None,
        top_n: int | None, manual: list[ManualSelection], retry_stocks: set[str] | None = None,
    ) -> dict[str, Any]:
        run, manifest, quant_rows = self.load_context(quant_run_id)
        pool = self.evaluation_rows(quant_rows, ranks=ranks, top_n=top_n, manual=manual)
        pool = self._filter_retry_stocks(pool, retry_stocks)
        profiles = [self.guard._profile(row.stock_code, run) for row in pool]
        failures = self.guard.real_gate_failures(run, manifest, profiles)
        coverage = []
        for row, profile in zip(pool, profiles):
            context = self.guard._structured_context(run, manifest, row, profile)
            context["stock_code"] = normalize_ts_code(row.stock_code)
            coverage.append(summarize_structured_input(context))
        return {
            "status": "DRY_RUN", "model_calls": 0, "quant_run_id": run.run_id,
            "manifest_id": manifest.manifest_id, "quant_scored_count": run.scored_count,
            "quant_rank_rows": len(quant_rows), "llm_evaluation_count": len(pool),
            "llm_evaluation_ranks": [row.rank for row in pool],
            "llm_evaluation_stock_codes": [row.stock_code for row in pool],
            "manual_selected_count": len(manual), "knowledge_mode": LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value,
            "real_gate_ready": not failures, "missing_gates": failures,
            "input_coverage": coverage,
        }

    def run(
        self, *, quant_run_id: str | None, ranks: Iterable[int] | None,
        top_n: int | None, manual: list[ManualSelection], selected_decisions: set[str],
        account_equity: Decimal, available_cash: Decimal,
        continue_on_stock_error: bool = True, retry_failed_only: bool = False,
        retry_stocks: set[str] | None = None, reuse_successful: bool = False,
        knowledge_mode: LLMKnowledgeMode = LLMKnowledgeMode.STRUCTURED_INPUT_ONLY,
        model_validation_top_n: int | None = None,
        analysis_only: bool = False,
        defer_candidate_generation: bool = False,
        concurrency: int = 1,
        batch_size: int = 10,
        checkpoint_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        run, manifest, quant_rows = self.load_context(quant_run_id, knowledge_mode)
        pool = self.evaluation_rows(quant_rows, ranks=ranks, top_n=top_n, manual=manual)
        pool = self._filter_retry_stocks(pool, retry_stocks)
        manual_map = {_canonical(item.stock_code): item for item in manual}
        profiles = [self.guard._profile(row.stock_code, run) for row in pool]
        failures = self.guard.real_gate_failures(run, manifest, profiles)
        if failures:
            raise ValueError("REAL_LLM_GUARDS_NOT_SATISFIED:" + ",".join(failures))

        validation_run_id = f"trader-demo-{uuid.uuid4().hex[:20]}"
        request_hash = hashlib.sha256(
            json.dumps({
                "validation_run_id": validation_run_id, "quant_run_id": run.run_id,
                "manifest": manifest.manifest_id, "stocks": [row.stock_code for row in pool],
                "manual": sorted(manual_map), "selected_decisions": sorted(selected_decisions),
                "profile_versions": [profile.profile_version for profile in profiles],
                "fundamental_prompt_version": FUNDAMENTAL_PROMPT_VERSION,
                "fundamental_contract_version": "fundamental_enrichment_wire_v4",
                "screening_prompt_version": SCREENING_PROMPT_VERSION,
                "screening_contract_version": "flash_component_wire_v4",
                "scoring_config_version": FLASH_SCORE_VERSION,
            }, sort_keys=True).encode()
        ).hexdigest()
        run_row = ModelValidationRun(
            run_id=validation_run_id, quant_run_id=run.run_id, run_data_manifest_id=manifest.manifest_id,
            run_mode=run.run_mode, knowledge_mode=knowledge_mode.value,
            decision_time=run.decision_time, base_market_trade_date=run.base_market_trade_date,
            target_trade_date=run.target_trade_date, real_llm=True, status="RUNNING", request_hash=request_hash,
            config_snapshot={
                "workflow": "TRADER_DEMO", "selected_decisions": sorted(selected_decisions),
                "manual": [item.__dict__ for item in manual], "account_equity": str(account_equity),
                "available_cash": str(available_cash), "model_alias": MODEL_ALIAS,
                "retry_failed_only": retry_failed_only, "retry_stocks": sorted(retry_stocks or []),
                "reuse_successful": reuse_successful,
                "model_validation_top_n": model_validation_top_n,
                "analysis_only": analysis_only,
                "defer_candidate_generation": defer_candidate_generation,
                "concurrency": concurrency,
                "batch_size": batch_size,
                "knowledge_mode": knowledge_mode.value,
                "fundamental_prompt_version": FUNDAMENTAL_PROMPT_VERSION,
                "fundamental_contract_version": "fundamental_enrichment_wire_v4",
                "screening_prompt_version": SCREENING_PROMPT_VERSION,
                "screening_contract_version": "flash_component_wire_v4",
                "flash_score_version": FLASH_SCORE_VERSION,
                "flash_ranking_version": FLASH_RANKING_VERSION,
            },
            expected_universe_audit=self.guard._expected_universe_audit(run.base_market_trade_date),
            warnings=[NON_ACTIONABLE_NOTICE],
        )
        self.session.add(run_row)
        self.session.commit()

        if concurrency <= 0 or batch_size <= 0:
            raise ValueError("INVALID_BATCH_EXECUTION_CONFIG")
        jobs: list[dict[str, Any]] = []
        reuse_tasks = retry_failed_only or reuse_successful
        for rank_row, profile in zip(pool, profiles):
            context = self.guard._structured_context(run, manifest, rank_row, profile)
            context["stock_code"] = normalize_ts_code(rank_row.stock_code)
            context["knowledge_mode"] = knowledge_mode.value
            context["input_quality"] = summarize_structured_input(context)
            if reuse_tasks:
                fundamental_reuse = self._find_reusable_task(
                    run, manifest, rank_row, profile, "fundamental_structured_inference", knowledge_mode
                )
                screening_reuse = self._find_reusable_task(
                    run, manifest, rank_row, profile, "structured_light_screening", knowledge_mode
                )
            else:
                reused = self.guard._find_reusable_sample(run, manifest, rank_row, profile)
                fundamental_reuse = self._task_from_sample(reused, "fundamental_structured_inference") if reused else None
                screening_reuse = self._task_from_sample(reused, "structured_light_screening") if reused else None
            jobs.append({
                "rank_row": rank_row, "profile": profile, "context": context,
                "fundamental_reuse": fundamental_reuse, "screening_reuse": screening_reuse,
            })

        batch_items: list[dict[str, Any]] = []
        all_audits: list[dict[str, Any]] = []
        successful_tasks = 0
        completed_stocks = 0
        for offset in range(0, len(jobs), batch_size):
            job_batch = jobs[offset:offset + batch_size]
            results: dict[int, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=min(concurrency, len(job_batch))) as executor:
                futures = {
                    executor.submit(self._execute_stock_analysis, job, run.run_mode, knowledge_mode): index
                    for index, job in enumerate(job_batch)
                }
                for future in as_completed(futures):
                    results[futures[future]] = future.result()

            for index in range(len(job_batch)):
                job = job_batch[index]
                result = results[index]
                rank_row = job["rank_row"]
                profile = job["profile"]
                context = job["context"]
                fundamental = result["fundamental"]
                screening = result["screening"]
                task_errors = result["task_errors"]
                audits = result["audits"]
                successful_tasks += result["successful_tasks"]
                for exc in result["exceptions"]:
                    matching = next((row for row in audits if row.get("task") == exc.task), audits[-1] if audits else {})
                    self._persist_failure_independently(validation_run_id, exc, matching)
                if task_errors and not continue_on_stock_error:
                    self._persist_audits(validation_run_id, audits)
                    self.session.commit()
                    raise result["exceptions"][0]

                decision = str(screening.get("screening_decision") or "PROVIDER_ERROR")
                llm_selected = (
                    not task_errors and decision in selected_decisions
                    if model_validation_top_n is None
                    else False
                )
                manual_item = manual_map.get(_canonical(rank_row.stock_code))
                selection_source = "BOTH" if llm_selected and manual_item else ("LLM" if llm_selected else ("MANUAL" if manual_item else ""))
                screening["_trader_demo"] = {
                    "execution_status": "SUCCESS" if not task_errors else "FAILED",
                    "errors": task_errors, "llm_selected": llm_selected,
                    "manual_selected": bool(manual_item), "manual_reason": manual_item.reason if manual_item else "",
                    "manual_priority": manual_item.priority if manual_item else "", "selection_source": selection_source,
                    "input_quality": context.get("input_quality") or {},
                }
                sample = ModelValidationSample(
                    validation_run_id=validation_run_id, quant_run_id=run.run_id,
                    run_data_manifest_id=manifest.manifest_id, rank=rank_row.rank,
                    stock_code=normalize_ts_code(rank_row.stock_code), stock_name=profile.stock_name or _meta(rank_row, "stock_name", rank_row.stock_code),
                    quant_scores=_quant_scores(rank_row), profile_version=profile.profile_version,
                    latest_financial_period=profile.latest_financial_period,
                    financial_available_at=profile.available_at, data_age_days=profile.financial_data_age_days,
                    selected_at=datetime.now(timezone.utc), fundamental_result=fundamental,
                    screening_result=screening, field_provenance=context["provenance"],
                    missing_fields=profile.missing_fields,
                )
                self.session.add(sample)
                self._persist_audits(validation_run_id, audits)
                all_audits.extend(audits)
                batch_items.append({
                    "rank_row": rank_row, "profile": profile, "fundamental": fundamental,
                    "screening": screening, "context": context, "selection_source": selection_source,
                    "manual_reason": manual_item.reason if manual_item else "", "sample": sample,
                })
            self.session.commit()
            completed_stocks += len(job_batch)
            if checkpoint_callback:
                checkpoint_callback({
                    "completed_stocks": completed_stocks, "total_stocks": len(pool),
                    "input_tokens": sum(int(row.get("input_tokens") or 0) for row in all_audits),
                    "output_tokens": sum(int(row.get("output_tokens") or 0) for row in all_audits),
                    "repair_input_tokens": sum(int((row.get("diagnostics") or {}).get("repair_input_tokens") or 0) for row in all_audits),
                    "repair_output_tokens": sum(int((row.get("diagnostics") or {}).get("repair_output_tokens") or 0) for row in all_audits),
                    "cost_usd": sum(float(row.get("cost_usd") or 0) for row in all_audits),
                })

        if model_validation_top_n is not None:
            successful_screening = [
                item["screening"] for item in batch_items
                if item["screening"].get("_trader_demo", {}).get("execution_status") == "SUCCESS"
            ]
            run_row.config_snapshot = {
                **dict(run_row.config_snapshot or {}),
                "flash_batch_quality": assert_flash_batch_quality(successful_screening),
            }
            flag_modified(run_row, "config_snapshot")
            selected = select_model_validation_items(batch_items, model_validation_top_n)
            if len(selected) < model_validation_top_n:
                raise ValueError(f"MODEL_VALIDATION_TOP_N_INSUFFICIENT:{len(selected)}/{model_validation_top_n}")
            selected_codes = {_canonical(item["rank_row"].stock_code) for item in selected}
            for item in batch_items:
                code = _canonical(item["rank_row"].stock_code)
                manual_item = manual_map.get(code)
                llm_selected = code in selected_codes
                selection_source = (
                    "BOTH" if llm_selected and manual_item
                    else "LLM_TOP20" if llm_selected
                    else "MANUAL" if manual_item
                    else ""
                )
                metadata = item["screening"].setdefault("_trader_demo", {})
                metadata["llm_selected"] = llm_selected
                metadata["model_validation_top20"] = llm_selected
                metadata["selection_source"] = selection_source
                metadata["selection_reason"] = (
                    "FLASH_V4_SCORE_DESC_CONFIDENCE_DESC_QUANT_RANK_ASC"
                    if llm_selected else "NOT_IN_FLASH_V4_TOP20"
                )
                metadata["flash_score_version"] = FLASH_SCORE_VERSION
                metadata["ranking_version"] = FLASH_RANKING_VERSION
                metadata["component_scores"] = {
                    key: item["screening"].get(key)
                    for key in (
                        "quant_consistency_score", "fundamental_quality_score",
                        "financial_quality_score", "risk_fit_score", "data_quality_score",
                        "data_quality_penalty", "risk_penalty",
                    )
                }
                item["selection_source"] = selection_source
                item["sample"].screening_result = item["screening"]
                flag_modified(item["sample"], "screening_result")
            self.session.commit()

        candidates = [] if analysis_only else [item for item in batch_items if item["selection_source"]]
        if candidates and not defer_candidate_generation:
            self._persist_candidate_outputs(
                validation_run_id, run, candidates, account_equity, available_cash
            )

        required_tasks = 2 * len(pool)
        complete_stocks = sum(
            1 for item in batch_items
            if item["screening"].get("_trader_demo", {}).get("execution_status") == "SUCCESS"
        )
        if complete_stocks == len(pool):
            final_status = "SUCCESS"
        elif successful_tasks == 0:
            final_status = "FAILED"
        else:
            final_status = "PARTIAL_SUCCESS"
        run_row.status = final_status
        run_row.warnings = sorted(set(run_row.warnings + (["LLM_BATCH_PARTIAL_FAILURE"] if final_status != "SUCCESS" else [])))
        self.session.commit()
        return validation_run_id

    def persist_model_validation_top_n(
        self,
        validation_run_id: str,
        *,
        top_n: int,
        manual: list[ManualSelection],
    ) -> list[str]:
        """Recompute and persist stable Top-N markers without another LLM call."""

        run_row = self.session.scalar(
            select(ModelValidationRun).where(ModelValidationRun.run_id == validation_run_id)
        )
        if run_row is None or run_row.status not in {"SUCCESS", "PARTIAL_SUCCESS"}:
            raise ValueError("COMPLETED_VALIDATION_RUN_REQUIRED")
        quant_rows = {
            normalize_ts_code(row.stock_code): row
            for row in self.session.scalars(
                select(QuantRankResult).where(QuantRankResult.quant_run_id == run_row.quant_run_id)
            )
        }
        samples = list(self.session.scalars(
            select(ModelValidationSample)
            .where(ModelValidationSample.validation_run_id == validation_run_id)
            .order_by(ModelValidationSample.rank)
        ))
        items = []
        for sample in samples:
            code = normalize_ts_code(sample.stock_code)
            rank_row = quant_rows.get(code)
            if rank_row is None:
                raise ValueError(f"VALIDATION_SAMPLE_QUANT_ROW_MISSING:{code}")
            items.append({
                "rank_row": rank_row,
                "profile": SimpleNamespace(
                    financial_status=(sample.fundamental_result or {}).get("financial_status") or {}
                ),
                "screening": dict(sample.screening_result or {}),
                "sample": sample,
            })
        selected = select_model_validation_items(items, top_n)
        if len(selected) != top_n:
            raise ValueError(f"MODEL_VALIDATION_TOP_N_INSUFFICIENT:{len(selected)}/{top_n}")
        selected_codes = {normalize_ts_code(item["rank_row"].stock_code) for item in selected}
        manual_map = {normalize_ts_code(item.stock_code): item for item in manual}
        for item in items:
            code = normalize_ts_code(item["rank_row"].stock_code)
            screening = json.loads(json.dumps(item["sample"].screening_result or {}, default=str))
            metadata = screening.setdefault("_trader_demo", {})
            is_selected = code in selected_codes
            is_manual = code in manual_map
            metadata.update({
                "llm_selected": is_selected,
                "model_validation_top20": is_selected,
                "manual_selected": is_manual,
                "selection_reason": (
                    "FLASH_V4_SCORE_DESC_CONFIDENCE_DESC_QUANT_RANK_ASC"
                    if is_selected else "NOT_IN_FLASH_V4_TOP20"
                ),
                "flash_score_version": FLASH_SCORE_VERSION,
                "ranking_version": FLASH_RANKING_VERSION,
                "component_scores": {
                    key: screening.get(key)
                    for key in (
                        "quant_consistency_score", "fundamental_quality_score",
                        "financial_quality_score", "risk_fit_score", "data_quality_score",
                        "data_quality_penalty", "risk_penalty",
                    )
                },
                "selection_source": (
                    "BOTH" if is_selected and is_manual
                    else "LLM_TOP20" if is_selected
                    else "MANUAL" if is_manual
                    else ""
                ),
            })
            if is_manual:
                metadata["manual_reason"] = manual_map[code].reason
                metadata["manual_priority"] = manual_map[code].priority
            item["sample"].screening_result = screening
            flag_modified(item["sample"], "screening_result")
        self.session.commit()
        return [normalize_ts_code(item["rank_row"].stock_code) for item in selected]

    def _execute_stock_analysis(
        self,
        job: dict[str, Any],
        run_mode: str,
        knowledge_mode: LLMKnowledgeMode,
    ) -> dict[str, Any]:
        """Run provider work without touching the SQLAlchemy session."""

        profile = job["profile"]
        context = job["context"]
        provider = StructuredValidationProvider()
        task_errors: list[dict[str, str]] = []
        exceptions: list[StructuredOutputValidationError] = []
        successful_tasks = 0

        fundamental_reuse = job.get("fundamental_reuse")
        if fundamental_reuse:
            source_sample, source_audit = fundamental_reuse
            fundamental = _normalize_fundamental(dict(source_sample.fundamental_result), profile)
            provider.audit.append(self._reused_task_audit(source_audit))
            successful_tasks += 1
        else:
            try:
                fundamental = _normalize_fundamental(
                    provider.fundamental(context, run_mode=run_mode, use_real_llm=True), profile
                )
                successful_tasks += 1
            except StructuredOutputValidationError as exc:
                task_errors.append(exc.as_dict())
                exceptions.append(exc)
                fundamental = _fundamental_fallback(profile, exc.category)

        screening_context = {
            "stock_code": job["rank_row"].stock_code,
            "quant": context["quant"],
            "fundamental_inference": fundamental,
            "financial_status": profile.financial_status,
            "missing_fields": profile.missing_fields,
            "provenance": context["provenance"],
            "input_quality": context.get("input_quality") or {},
            "knowledge_mode": knowledge_mode.value,
        }
        screening_reuse = job.get("screening_reuse")
        if screening_reuse:
            source_sample, source_audit = screening_reuse
            screening = dict(source_sample.screening_result)
            screening.pop("_trader_demo", None)
            screening.pop("_pro", None)
            provider.audit.append(self._reused_task_audit(source_audit))
            successful_tasks += 1
        else:
            try:
                screening = provider.screening(screening_context, run_mode=run_mode, use_real_llm=True)
                successful_tasks += 1
            except StructuredOutputValidationError as exc:
                task_errors.append(exc.as_dict())
                exceptions.append(exc)
                screening = _screening_failure(exc.category, exc.field, profile.missing_fields)

        return {
            "fundamental": fundamental,
            "screening": screening,
            "task_errors": task_errors,
            "exceptions": exceptions,
            "audits": provider.audit,
            "successful_tasks": successful_tasks,
        }

    def generate_candidate_outputs(
        self,
        validation_run_id: str,
        *,
        account_equity: Decimal,
        available_cash: Decimal,
    ) -> None:
        run_row = self.session.scalar(
            select(ModelValidationRun).where(ModelValidationRun.run_id == validation_run_id)
        )
        if run_row is None or run_row.status not in {"SUCCESS", "PARTIAL_SUCCESS"}:
            raise ValueError("COMPLETED_VALIDATION_RUN_REQUIRED")
        quant_run = self.session.scalar(
            select(QuantRun).where(QuantRun.run_id == run_row.quant_run_id)
        )
        if quant_run is None:
            raise ValueError("QUANT_RUN_REQUIRED")
        quant_rows = {
            normalize_ts_code(row.stock_code): row
            for row in self.session.scalars(
                select(QuantRankResult).where(QuantRankResult.quant_run_id == quant_run.run_id)
            ).all()
        }
        samples = self.session.scalars(
            select(ModelValidationSample).where(
                ModelValidationSample.validation_run_id == validation_run_id
            ).order_by(ModelValidationSample.rank)
        ).all()
        candidates = []
        for sample in samples:
            screening = dict(sample.screening_result or {})
            metadata = screening.get("_trader_demo") or {}
            selection_source = str(metadata.get("selection_source") or "")
            if not selection_source:
                continue
            rank_row = quant_rows.get(normalize_ts_code(sample.stock_code))
            if rank_row is None:
                raise ValueError(f"CANDIDATE_QUANT_ROW_MISSING:{sample.stock_code}")
            profile = self.guard._profile(rank_row.stock_code, quant_run)
            candidates.append(
                {
                    "rank_row": rank_row,
                    "profile": profile,
                    "fundamental": dict(sample.fundamental_result or {}),
                    "screening": screening,
                    "context": {},
                    "selection_source": selection_source,
                    "manual_reason": str(metadata.get("manual_reason") or ""),
                    "sample": sample,
                }
            )
        self._persist_candidate_outputs(
            validation_run_id, quant_run, candidates, account_equity, available_cash
        )

    def _persist_candidate_outputs(
        self,
        validation_run_id: str,
        run: QuantRun,
        candidates: list[dict[str, Any]],
        account_equity: Decimal,
        available_cash: Decimal,
    ) -> None:
        if self.session.scalar(select(ModelValidationOrderPlan.id).where(ModelValidationOrderPlan.validation_run_id == validation_run_id)):
            raise ValueError("CANDIDATE_OUTPUTS_ALREADY_EXIST")
        plans: list[dict[str, Any]] = []
        for item in candidates:
            original_screening = item["screening"]
            plan_item = dict(item)
            if original_screening.get("_trader_demo", {}).get("execution_status") != "SUCCESS":
                plan_item["screening"] = {**original_screening, "screening_decision": "REJECT", "confidence": 0}
            try:
                plan = self.guard._build_order_plan(run, plan_item)
            except Exception as exc:
                plan = _blocked_plan(run, item["rank_row"].stock_code, f"ORDER_PLAN_ERROR:{type(exc).__name__}")
            warnings = list(plan.get("warnings") or [])
            if item["selection_source"] == "MANUAL" and original_screening.get("screening_decision") != "ADVANCE":
                warnings.append("人工选择，但LLM未入选")
            if original_screening.get("_trader_demo", {}).get("execution_status") != "SUCCESS":
                warnings.append("人工选择，但LLM分析失败")
            plan["warnings"] = sorted(set(warnings))
            plans.append(plan)
            self.session.add(ModelValidationOrderPlan(validation_run_id=validation_run_id, **plan))
        self.session.commit()

        snapshot_id = f"trader-demo-account-{uuid.uuid4().hex[:16]}"
        allocation_run_id = f"trader-demo-allocation-{uuid.uuid4().hex[:16]}"
        self.session.add(ValidationAccountSnapshot(
            snapshot_id=snapshot_id, validation_run_id=validation_run_id,
            account_equity=account_equity, available_cash=available_cash,
            snapshot_time=run.decision_time, existing_positions=[],
        ))
        allocations = self.guard._size_positions(candidates, plans, account_equity, available_cash) if candidates else []
        by_code = {item["stock_code"]: item for item in allocations}
        for candidate, plan in zip(candidates, plans):
            code = candidate["rank_row"].stock_code
            allocation = by_code.get(code) or _zero_allocation(code, "POSITION_SIZING_NO_RESULT")
            if plan.get("recommended_price") is None or plan.get("status") == "BLOCKED":
                allocation = _zero_allocation(code, "RISK_GATE_BLOCKED_OR_NO_RECOMMENDED_PRICE")
            self.session.add(ModelValidationAllocation(
                validation_run_id=validation_run_id, allocation_run_id=allocation_run_id,
                account_snapshot_id=snapshot_id, **allocation,
            ))

        self.session.commit()

    def readback(self, validation_run_id: str) -> dict[str, Any]:
        base = self.guard.readback(validation_run_id)
        quant_run_id = base["run"].quant_run_id
        base["quant_rows"] = list(self.session.scalars(
            select(QuantRankResult).where(QuantRankResult.quant_run_id == quant_run_id).order_by(QuantRankResult.rank)
        ))
        return base

    @staticmethod
    def _filter_retry_stocks(rows: list[QuantRankResult], retry_stocks: set[str] | None) -> list[QuantRankResult]:
        if not retry_stocks:
            return rows
        wanted = {normalize_ts_code(item) for item in retry_stocks}
        selected = [row for row in rows if normalize_ts_code(row.stock_code) in wanted]
        missing = wanted - {normalize_ts_code(row.stock_code) for row in selected}
        if missing:
            raise ValueError("RETRY_STOCK_NOT_IN_EVALUATION_POOL:" + ",".join(sorted(missing)))
        return selected

    def _find_reusable_task(
        self, run: QuantRun, manifest: RunDataManifestRecord, rank_row: QuantRankResult,
        profile: Any, task: str, knowledge_mode: LLMKnowledgeMode,
    ) -> tuple[ModelValidationSample, ModelValidationLLMAudit] | None:
        samples = list(self.session.scalars(
            select(ModelValidationSample)
            .join(ModelValidationRun, ModelValidationRun.run_id == ModelValidationSample.validation_run_id)
            .where(
                ModelValidationRun.real_llm.is_(True),
                ModelValidationRun.status.in_(["COMPLETED", "SUCCESS", "PARTIAL_SUCCESS"]),
                ModelValidationRun.knowledge_mode == knowledge_mode.value,
                ModelValidationSample.quant_run_id == run.run_id,
                ModelValidationSample.run_data_manifest_id == manifest.manifest_id,
                ModelValidationSample.stock_code.in_([rank_row.stock_code, normalize_ts_code(rank_row.stock_code)]),
                ModelValidationSample.rank == rank_row.rank,
                ModelValidationSample.profile_version == profile.profile_version,
            ).order_by(ModelValidationSample.id.desc())
        ))
        for sample in samples:
            audit = self.session.scalar(
                select(ModelValidationLLMAudit).where(
                    ModelValidationLLMAudit.validation_run_id == sample.validation_run_id,
                    ModelValidationLLMAudit.stock_code.in_([sample.stock_code, normalize_ts_code(sample.stock_code)]),
                    ModelValidationLLMAudit.task == task,
                    ModelValidationLLMAudit.prompt_version == (
                        FUNDAMENTAL_PROMPT_VERSION
                        if task == "fundamental_structured_inference"
                        else SCREENING_PROMPT_VERSION
                    ),
                    ModelValidationLLMAudit.status.in_(["ok", "SUCCESS"]),
                    ModelValidationLLMAudit.schema_status == "PASS",
                ).order_by(ModelValidationLLMAudit.id.desc())
            )
            if audit is not None:
                return sample, audit
        return None

    def _task_from_sample(
        self, sample: ModelValidationSample, task: str,
    ) -> tuple[ModelValidationSample, ModelValidationLLMAudit] | None:
        prompt_version = FUNDAMENTAL_PROMPT_VERSION if task == "fundamental_structured_inference" else SCREENING_PROMPT_VERSION
        audit = self.session.scalar(select(ModelValidationLLMAudit).where(
            ModelValidationLLMAudit.validation_run_id == sample.validation_run_id,
            ModelValidationLLMAudit.stock_code.in_([sample.stock_code, normalize_ts_code(sample.stock_code)]),
            ModelValidationLLMAudit.task == task,
            ModelValidationLLMAudit.prompt_version == prompt_version,
            ModelValidationLLMAudit.status.in_(["ok", "SUCCESS"]),
            ModelValidationLLMAudit.schema_status == "PASS",
        ).order_by(ModelValidationLLMAudit.id.desc()))
        return (sample, audit) if audit else None

    @staticmethod
    def _reused_task_audit(source: ModelValidationLLMAudit) -> dict[str, Any]:
        return {
            "stock_code": normalize_ts_code(source.stock_code), "task": source.task,
            "knowledge_mode": source.knowledge_mode, "model_alias": source.model_alias,
            "actual_model": source.actual_model, "prompt_version": source.prompt_version,
            "status": "ok", "schema_status": "PASS", "request_hash": source.request_hash,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": Decimal("0"), "latency_ms": 0,
            "cache_status": "REUSED", "error_category": "", "error_field": "", "error_message": "",
            "diagnostics": {
                **dict(source.diagnostics or {}),
                "reused": True, "source_validation_run_id": source.validation_run_id,
                "source_prompt_version": source.prompt_version,
            },
        }

    def _persist_failure_independently(
        self, validation_run_id: str, exc: StructuredOutputValidationError, audit: dict[str, Any],
    ) -> None:
        independent = get_session(self.session.get_bind())
        try:
            independent.add(ModelValidationFailureAudit(
                validation_run_id=validation_run_id, stock_code=normalize_ts_code(exc.stock_code),
                task=exc.task, attempt_id=f"attempt-{uuid.uuid4().hex[:20]}",
                prompt_version=exc.prompt_version, status="FAILED",
                error_category=exc.category, error_field=exc.field,
                error_message=str(exc.detail)[:240], diagnostics=dict(audit.get("diagnostics") or {}),
            ))
            independent.commit()
        finally:
            independent.close()

    def _persist_audits(self, validation_run_id: str, audits: list[dict[str, Any]]) -> None:
        fields = (
            "stock_code", "task", "knowledge_mode", "model_alias", "actual_model", "prompt_version",
            "status", "schema_status", "request_hash", "input_tokens", "output_tokens", "cost_usd",
            "latency_ms", "cache_status", "error_category", "error_field", "error_message",
            "diagnostics",
        )
        for audit in audits:
            self.session.add(ModelValidationLLMAudit(
                validation_run_id=validation_run_id,
                **{key: audit.get(key) for key in fields},
            ))

    def _ensure_audit_columns(self) -> None:
        bind = self.session.get_bind()
        names = {column["name"] for column in inspect(bind).get_columns("model_validation_llm_audit")}
        additions = {
            "error_category": "VARCHAR(64)", "error_field": "VARCHAR(160)", "error_message": "TEXT",
            "diagnostics": "JSON NOT NULL DEFAULT '{}'",
        }
        for name, sql_type in additions.items():
            if name not in names:
                self.session.execute(text(f"ALTER TABLE model_validation_llm_audit ADD COLUMN {name} {sql_type}"))
        order_names = {
            column["name"] for column in inspect(bind).get_columns("model_validation_order_plan")
        }
        order_additions = {
            "risk_reward_to_tp1": "NUMERIC(10, 4)",
            "risk_reward_to_tp2": "NUMERIC(10, 4)",
            "active_risk_reward": "NUMERIC(10, 4)",
            "active_target_mode": "VARCHAR(32)",
            "unrounded_stop_loss_price": "NUMERIC(14, 4)",
        }
        for name, sql_type in order_additions.items():
            if name not in order_names:
                self.session.execute(text(
                    f"ALTER TABLE model_validation_order_plan ADD COLUMN {name} {sql_type}"
                ))
        self.session.commit()


def select_model_validation_items(items: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    if top_n <= 0:
        raise ValueError("MODEL_VALIDATION_TOP_N_MUST_BE_POSITIVE")
    eligible = []
    for item in items:
        screening = item.get("screening") or {}
        metadata = screening.get("_trader_demo") or {}
        profile = item.get("profile")
        financial = getattr(profile, "financial_status", {}) or {}
        financial_status = str(financial.get("status") or "UNKNOWN").upper()
        score = screening.get("llm_score")
        if metadata.get("execution_status") != "SUCCESS" or score is None:
            continue
        if financial_status in {"BLOCK", "BLOCKED", "HIGH_RISK", "BLACK_SWAN"}:
            continue
        eligible.append(item)
    eligible.sort(
        key=lambda item: (
            -float(item["screening"].get("llm_score") or 0),
            -float(item["screening"].get("confidence") or 0),
            int(item["rank_row"].rank),
            normalize_ts_code(item["rank_row"].stock_code),
        )
    )
    return eligible[:top_n]


def _canonical(value: str) -> str:
    return display_stock_code(value)


def _meta(row: QuantRankResult, key: str, default: Any = "") -> Any:
    return (row.factor_detail_reference or {}).get(key, default)


def _fundamental_fallback(profile: Any, category: str) -> dict[str, Any]:
    return {
        "analysis_status": "FAILED", "error_category": category,
        "industry_chain": {"chain_name": None, "chain_position": "UNKNOWN", "source_status": "UNKNOWN"},
        "level_one_sector_explanation": {"summary": profile.level_one_sector or "信息不足", "source_status": "VERIFIED_STRUCTURED"},
        "main_business_summary": {"summary": profile.main_business or "信息不足", "source_status": "VERIFIED_STRUCTURED", "display_marker": ""},
        "industry_position": {"description": "分析失败", "source_status": "UNKNOWN"},
        "concept_tags": profile.normalized_concept_tags or profile.source_concept_tags or [],
        "competitive_advantage": {"summary": "分析失败", "source_status": "UNKNOWN"},
        "industry_trend": {"summary": "分析失败", "source_status": "UNKNOWN"},
        "investment_logic": {"summary": "分析失败", "source_status": "UNKNOWN"},
        "domestic_substitution": {"level": "INSUFFICIENT_DATA", "source_status": "UNKNOWN"},
        "observation_rating": "INSUFFICIENT_DATA", "financial_status": profile.financial_status,
        "structural_theme_fit": {"value": "UNKNOWN", "source_status": "UNKNOWN"},
    }


def _screening_failure(category: str, field: str, missing: list[str]) -> dict[str, Any]:
    return {
        "screening_decision": category, "llm_score": None, "confidence": 0,
        "reason": "分析失败", "risk_note": f"{category}:{field}", "data_conflict": False,
        "requires_manual_review": True, "evidence_fields": [], "missing_data": list(missing),
    }


def _blocked_plan(run: QuantRun, stock_code: str, warning: str) -> dict[str, Any]:
    return {
        "quant_run_id": run.run_id, "run_data_manifest_id": run.data_manifest_id,
        "stock_code": stock_code, "status": "BLOCKED", "decision_time": run.decision_time,
        "base_market_trade_date": run.base_market_trade_date, "target_trade_date": run.target_trade_date,
        "factor_version": run.factor_version, "config_snapshot": {}, "conservative_price": None,
        "balanced_price": None, "aggressive_price": None, "recommended_price": None,
        "max_acceptable_price": None, "stop_loss_price": None, "take_profit_1_price": None,
        "take_profit_2_price": None, "fill_probability": None, "risk_reward": None,
        "order_price_score": None, "support": None, "resistance": None, "atr": None, "vwap": None,
        "previous_close": None, "limit_up_estimated": None, "limit_down_estimated": None,
        "cancel_conditions": {}, "reprice_conditions": {}, "warnings": [NON_ACTIONABLE_NOTICE, warning],
        "temporal_status": run.temporal_status,
    }


def _zero_allocation(stock_code: str, warning: str) -> dict[str, Any]:
    return {
        "stock_code": stock_code, "relative_allocation_weight": Decimal("0"),
        "suggested_position_percent": Decimal("0"), "suggested_capital_amount": Decimal("0"),
        "suggested_quantity": 0, "estimated_max_loss": Decimal("0"),
        "binding_constraints": [warning], "warnings": [NON_ACTIONABLE_NOTICE, warning],
    }
