from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend.application.excel_export import DailyExcelExportService
from backend.application.pro_v3 import ProductionProV3ApplicationService
from backend.core.runtime_paths import output_root, report_root, tushare_cache_root
from backend.workbench.historical import HistoricalPipelineRunResolver
from database.models.quant_run import QuantRankResult, QuantRun
from database.models.system import LLMUsage
from database.models.validation import (
    ModelValidationLLMAudit,
    ModelValidationRun,
    ModelValidationSample,
    ProResumeRun,
)
from database.models.workbench import ManualSelectionRecord, PipelineJob
from database.models.internal_auth import JobExecutionLock
from database.session import get_session, init_db
from database.stock_master_sync import StockMasterSyncService
from datasource.tushare_provider import TushareMarketDataProvider
from market_review.service import MarketReviewService
from quant.run_repository import QuantRunRepository
from services.ranking_evaluation.constants import load_config as load_ranking_evaluation_config
from services.ranking_evaluation.data_quality_service import RankingDataQualityService
from services.ranking_evaluation.snapshot_service import RankingSnapshotService
from research.knowledge_mode import LLMKnowledgeMode
from scripts.prewarm_tushare_trade_date_cache import run_prewarm
from scripts.run_real_quant_top500 import run_real_quant_top500
from temporal.readiness import DataReadinessService
from temporal.schemas import RunMode, TemporalStatus
from trader_demo.runtime import temporary_real_llm_runtime
from trader_demo.budget import PipelineBudgetConfig
from trader_demo.service import ManualSelection, TraderDemoService


ACTIVE = {"PENDING", "RUNNING", "CANCELLATION_REQUESTED"}
JOB_TYPES = {"DATA", "QUANT", "FLASH", "FINAL", "EXPORT", "MARKET_DAILY_REVIEW"}


class WorkflowApplicationService:
    """Persistent application service shared by HTTP and desktop entry points."""

    def __init__(self, session) -> None:
        self.session = session

    def start(
        self,
        job_type: str,
        trade_date: date,
        options: dict[str, Any] | None = None,
        *,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = job_type.upper()
        if normalized not in JOB_TYPES:
            raise ValueError("INVALID_JOB_TYPE")
        options = _safe_options(options or {})
        actor = _safe_actor(actor)
        request_hash = hashlib.sha256(json.dumps({"job_type": normalized, "trade_date": str(trade_date), "options": options}, sort_keys=True).encode()).hexdigest()
        active = self.session.scalar(select(PipelineJob).where(
            PipelineJob.job_type == normalized,
            PipelineJob.trade_date == trade_date,
            PipelineJob.status.in_(ACTIVE),
        ).order_by(PipelineJob.created_at.desc()))
        if active is not None:
            return _job_payload(active) | {"duplicate_status": "ACTIVE_JOB_REUSED", "code": "JOB_ALREADY_RUNNING"}
        completed = self.session.scalar(select(PipelineJob).where(
            PipelineJob.job_type == normalized,
            PipelineJob.trade_date == trade_date,
            PipelineJob.status == "SUCCESS",
        ).order_by(PipelineJob.created_at.desc()))
        if completed is not None and (completed.checkpoint or {}).get("request_hash") == request_hash and not options.get("force"):
            return _job_payload(completed) | {"duplicate_status": "SUCCESS_CACHE_HIT"}
        lock_key = f"{normalized}:{trade_date.isoformat()}"
        existing_lock = self.session.scalar(select(JobExecutionLock).where(JobExecutionLock.lock_key == lock_key))
        if existing_lock is not None:
            locked_job = self.session.scalar(select(PipelineJob).where(PipelineJob.job_id == existing_lock.job_id))
            if locked_job is not None and locked_job.status in ACTIVE and _aware(existing_lock.expires_at) > datetime.now(timezone.utc):
                return _job_payload(locked_job) | {"duplicate_status": "ACTIVE_JOB_REUSED", "code": "JOB_ALREADY_RUNNING"}
            self.session.delete(existing_lock)
            self.session.commit()
        row = PipelineJob(
            job_id=f"desktop-{uuid.uuid4().hex[:20]}", job_type=normalized, trade_date=trade_date,
            status="PENDING", stage="QUEUED", progress_current=0, progress_total=1,
            success_count=0, failure_count=0, token_usage=0, cost_usd=0.0,
            run_ids={}, checkpoint={
                "request_hash": request_hash,
                "options": options,
                "mode": "REAL_APPLICATION_SERVICE",
                "started_by": actor["email"],
                "started_by_role": actor["role"],
            },
        )
        self.session.add(row)
        self.session.add(JobExecutionLock(
            lock_key=lock_key,
            job_id=row.job_id,
            started_by=actor["email"],
            current_stage="QUEUED",
            acquired_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        ))
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            concurrent_lock = self.session.scalar(select(JobExecutionLock).where(JobExecutionLock.lock_key == lock_key))
            concurrent = self.session.scalar(select(PipelineJob).where(
                PipelineJob.job_id == concurrent_lock.job_id
            )) if concurrent_lock else None
            if concurrent is None:
                raise
            return _job_payload(concurrent) | {"duplicate_status": "ACTIVE_JOB_REUSED", "code": "JOB_ALREADY_RUNNING"}
        return _job_payload(row) | {"duplicate_status": "NEW_JOB"}

    def cancel(self, job_id: str) -> dict[str, Any]:
        row = self._job(job_id)
        if row.status in {"PENDING", "RUNNING"}:
            row.status = "CANCELLATION_REQUESTED"
            row.stage = "CANCELLATION_REQUESTED"
            self.session.commit()
        return _job_payload(row)

    def resume(self, job_id: str, *, actor: dict[str, Any] | None = None) -> dict[str, Any]:
        row = self._job(job_id)
        if row.status not in {"FAILED", "CANCELLED", "PARTIAL_SUCCESS"}:
            raise ValueError("JOB_NOT_RESUMABLE")
        options = dict((row.checkpoint or {}).get("options") or {}) | {"resume_from_job_id": row.job_id, "force": True}
        return self.start(row.job_type, row.trade_date, options, actor=actor)

    def _job(self, job_id: str) -> PipelineJob:
        row = self.session.scalar(select(PipelineJob).where(PipelineJob.job_id == job_id))
        if row is None: raise ValueError("JOB_NOT_FOUND")
        return row


def execute_persisted_job(job_id: str) -> None:
    init_db()
    session = get_session()
    try:
        job = session.scalar(select(PipelineJob).where(PipelineJob.job_id == job_id))
        if job is None or job.status not in {"PENDING", "CANCELLATION_REQUESTED"}: return
        if job.status == "CANCELLATION_REQUESTED":
            job.status, job.stage, job.finished_at = "CANCELLED", "CANCELLED", datetime.now(timezone.utc)
            _release_job_lock(session, job.job_id); session.commit(); return
        job.status, job.stage, job.started_at = "RUNNING", "STARTING", datetime.now(timezone.utc)
        session.commit()
        options = dict((job.checkpoint or {}).get("options") or {})
        handlers = {
            "DATA": _run_data,
            "QUANT": _run_quant,
            "FLASH": _run_flash,
            "FINAL": _run_final,
            "EXPORT": _run_export,
            "MARKET_DAILY_REVIEW": _run_market_review,
        }
        result = handlers[job.job_type](session, job, options)
        _check_cancelled(session, job)
        _mark_job_success(job, result)
        _release_job_lock(session, job.job_id)
        session.commit()
    except JobCancelled:
        job.status, job.stage, job.finished_at = "CANCELLED", "CANCELLED", datetime.now(timezone.utc)
        _release_job_lock(session, job.job_id)
        session.commit()
    except Exception as exc:
        job.status, job.stage = "FAILED", "FAILED"
        job.failure_count = max(int(job.failure_count or 0), 1)
        job.error_code = type(exc).__name__[:128]
        job.error_message = _safe_error(exc)
        job.finished_at = datetime.now(timezone.utc)
        _release_job_lock(session, job.job_id)
        session.commit()
    finally:
        session.close()


def _run_data(session, job: PipelineJob, options: dict[str, Any]) -> dict[str, Any]:
    _require_secret("TUSHARE_TOKEN")
    job.stage = "TUSHARE_BATCH_UPDATE"; session.commit()
    report_path = report_root() / f"data_update_{job.trade_date:%Y%m%d}_{job.job_id}.json"
    report = run_prewarm(
        start_date=job.trade_date.isoformat(), end_date=job.trade_date.isoformat(),
        interfaces=["daily", "daily_basic", "moneyflow", "stk_limit", "adj_factor"],
        include_reference=True, refresh_cache=bool(options.get("force")), output=report_path, progress=False,
    )
    if int(report.get("error_count") or 0) > 0:
        raise RuntimeError("DATA_UPDATE_INCOMPLETE")
    stock_basic = TushareMarketDataProvider(cache_enabled=True).get_stock_basic_result(
        use_cache=not bool(options.get("force"))
    )
    if not stock_basic.available:
        raise RuntimeError("STOCK_BASIC_UPDATE_FAILED")
    sync = StockMasterSyncService(session, tushare_cache_root() / "fundamental" / "stock_basic").sync_from_cache()
    return {"run_ids": {"data_report": report_path.name}, "rows_updated": report.get("row_count"), "stock_master": sync.as_dict()}


def _run_quant(session, job: PipelineJob, options: dict[str, Any]) -> dict[str, Any]:
    _require_secret("TUSHARE_TOKEN")
    before_llm = int(session.scalar(select(LLMUsage.id).order_by(LLMUsage.id.desc())) or 0)
    decision_time = datetime.combine(job.trade_date, time(15, 30), tzinfo=ZoneInfo("Asia/Shanghai"))
    context, manifest = DataReadinessService().check(
        RunMode.POST_MARKET_FINAL, decision_time, base_market_trade_date=job.trade_date,
    )
    if manifest.temporal_status is TemporalStatus.BLOCKED or not manifest.actionable:
        raise RuntimeError("TEMPORAL_CONSISTENCY_BLOCKED")
    job.stage = "QUANT_NO_LLM"; session.commit()
    settings = _workbench_settings(session)
    report_path = report_root() / f"quant_{job.trade_date:%Y%m%d}_{job.job_id}.json"
    report = run_real_quant_top500(
        provider="tushare", history_provider="tushare", backup_history_provider="baostock",
        top_n=int(settings["quant_top_n"]), sample_limit=0, trade_date=job.trade_date.isoformat(),
        save_to_db=False, output=report_path, progress=False, use_cache=True,
        refresh_cache=bool(options.get("force")), data_fetch_workers=int(options.get("data_fetch_workers") or 1), factor_workers="1",
    )
    row = QuantRunRepository(session).save_report(
        report, run_mode=RunMode.POST_MARKET_FINAL.value, decision_time=context.decision_time,
        base_trade_date=context.base_market_trade_date, target_trade_date=context.target_trade_date,
        manifest_id=manifest.id, temporal_status=manifest.temporal_status.value, actionable=manifest.actionable,
        config_snapshot={"top_n": settings["quant_top_n"], "provider": "tushare", "history_provider": "tushare"},
    )
    ranking_capture = {"status": "DISABLED"}
    if bool(load_ranking_evaluation_config().get("auto_capture_after_quant", True)):
        try:
            ranking_capture = RankingSnapshotService(session).capture(
                trade_date=job.trade_date,
                source_quant_run_id=row.run_id,
                factor_version="TUSHARE_BASELINE_V1",
            )
        except Exception as exc:
            # Quant is already committed. Evaluation is deliberately fail-open
            # for production ranking, but its own failure remains auditable.
            session.rollback()
            RankingDataQualityService(session).record(
                issue_code="RANKING_EVALUATION_CAPTURE_FAILED",
                issue_level="ABNORMAL",
                affected_date=job.trade_date,
                affected_version="TUSHARE_BASELINE_V1",
                detail=f"{type(exc).__name__}: {_safe_error(exc)}",
            )
            session.commit()
            ranking_capture = {
                "status": "WARNING",
                "warning_code": "RANKING_EVALUATION_CAPTURE_FAILED",
            }
    ranking_capture = json.loads(json.dumps(ranking_capture, ensure_ascii=False, default=str))
    after_llm = int(session.scalar(select(LLMUsage.id).order_by(LLMUsage.id.desc())) or 0)
    if before_llm != after_llm or not row.no_llm_call_verified:
        raise RuntimeError("QUANT_ZERO_LLM_ASSERTION_FAILED")
    return {
        "run_ids": {"quant_run_id": row.run_id, "manifest_id": manifest.id},
        "scored_count": row.scored_count,
        "no_llm_call_verified": True,
        "ranking_evaluation_capture": ranking_capture,
    }


def _run_flash(session, job: PipelineJob, options: dict[str, Any]) -> dict[str, Any]:
    _require_secret("DEEPSEEK_API_KEY")
    if not options.get("confirm_budget"):
        raise ValueError("LLM_BUDGET_CONFIRMATION_REQUIRED")
    quant_query = select(QuantRun).where(
        QuantRun.base_market_trade_date == job.trade_date,
        QuantRun.status == "COMPLETED", QuantRun.actionable.is_(True), QuantRun.no_llm_call_verified.is_(True),
    )
    requested_quant_run_id = str(options.get("quant_run_id") or "").strip()
    if requested_quant_run_id:
        quant_query = quant_query.where(QuantRun.run_id == requested_quant_run_id)
    quant = session.scalar(quant_query.order_by(QuantRun.created_at.desc()))
    if quant is None: raise ValueError("ACTIONABLE_QUANT_RUN_REQUIRED")
    manual_rows = list(session.scalars(
        select(ManualSelectionRecord)
        .where(
            ManualSelectionRecord.trade_date == job.trade_date
        )
        .order_by(
            ManualSelectionRecord.created_at,
            ManualSelectionRecord.stock_code,
        )
    ))
    quant_codes = set(session.scalars(
        select(QuantRankResult.stock_code).where(
            QuantRankResult.quant_run_id == quant.run_id
        )
    ))
    eligible_manual_rows, excluded_manual_rows = _partition_manual_rows(
        manual_rows,
        quant_codes,
        include_review_only=True,
    )
    review_only_manual_rows = [
        row
        for row in eligible_manual_rows
        if str(row.stock_code or "").strip().upper().split(".", 1)[0]
        not in {
            str(code or "").strip().upper().split(".", 1)[0]
            for code in quant_codes
        }
    ]
    manual = [
        ManualSelection(row.stock_code, row.reason, row.priority)
        for row in eligible_manual_rows
    ]
    settings = _workbench_settings(session)
    flash_limit = min(int(settings["daily_token_limit"]), PipelineBudgetConfig().flash_limit)
    existing_flash_tokens = _flash_tokens_for_date(session, job.trade_date)
    if existing_flash_tokens >= flash_limit:
        raise RuntimeError("FLASH_TOKEN_BUDGET_EXCEEDED")
    job.stage = "FUNDAMENTAL_V4_FLASH_V5"
    job.token_usage = existing_flash_tokens
    checkpoint_state = dict(job.checkpoint or {})
    checkpoint_state["budget"] = {
        "stage": "FLASH", "used_before": existing_flash_tokens,
        "limit": flash_limit, "remaining_before": flash_limit - existing_flash_tokens,
    }
    checkpoint_state["manual_pool"] = {
        "requested_count": len(manual_rows),
        "included_count": len(eligible_manual_rows),
        "excluded_count": len(excluded_manual_rows),
        "forced_review_count": len(review_only_manual_rows),
        "forced_review_codes": [row.stock_code for row in review_only_manual_rows],
        "policy": "ALL_MANUAL_TO_FLASH_AND_PRO_REVIEW_ST_HARD_GATE_RETAINED",
        "excluded": [
            {
                "stock_code": row.stock_code,
                "reason": "NOT_IN_ACTIONABLE_QUANT_UNIVERSE",
            }
            for row in excluded_manual_rows
        ],
    }
    job.checkpoint = checkpoint_state
    session.commit()

    def checkpoint(payload: dict[str, Any]) -> None:
        session.refresh(job)
        if job.status == "CANCELLATION_REQUESTED": raise JobCancelled()
        job.progress_current = int(
            payload.get("completed_stocks") or payload.get("completed")
            or payload.get("success_count") or 0
        )
        job.progress_total = int(
            payload.get("total_stocks") or payload.get("total")
            or settings["llm_analysis_n"]
        )
        job.current_stock = payload.get("current_stock")
        current_tokens = int(payload.get("input_tokens") or 0) + int(payload.get("output_tokens") or 0)
        job.token_usage = existing_flash_tokens + current_tokens
        job.cost_usd = float(payload.get("cost_usd") or 0)
        job.success_count = int(payload.get("success_count") or 0)
        job.failure_count = int(payload.get("failure_count") or 0)
        state = dict(job.checkpoint or {})
        state["budget"] = {
            "stage": "FLASH", "used_before": existing_flash_tokens,
            "current_run": current_tokens, "used_total": job.token_usage,
            "limit": flash_limit, "remaining": max(flash_limit - job.token_usage, 0),
        }
        job.checkpoint = state
        session.commit()
        if job.token_usage > flash_limit:
            raise RuntimeError("FLASH_TOKEN_BUDGET_EXCEEDED")

    try:
        with temporary_real_llm_runtime():
            validation_run_id = TraderDemoService(session).run(
                quant_run_id=quant.run_id, ranks=None, top_n=int(settings["llm_analysis_n"]), manual=manual,
                selected_decisions={"ADVANCE", "HOLD", "WATCH_ONLY", "REJECT"},
                account_equity=Decimal(str(settings["validation_account_equity"])),
                available_cash=Decimal(str(settings["validation_available_cash"])),
                continue_on_stock_error=True, reuse_successful=True,
                knowledge_mode=LLMKnowledgeMode.STRUCTURED_INPUT_ONLY,
                model_validation_top_n=int(settings["llm_top_n"]), defer_candidate_generation=True,
                concurrency=int(settings["flash_concurrency"]), batch_size=int(settings["flash_batch_size"]),
                checkpoint_callback=checkpoint,
                allow_manual_outside_quant=True,
            )
    except Exception as exc:
        _finalize_interrupted_flash_run(session, quant.run_id, exc)
        raise
    return {
        "run_ids": {"quant_run_id": quant.run_id, "flash_run_id": validation_run_id},
        "manual_count": len(manual),
        "manual_excluded_count": len(excluded_manual_rows),
        "manual_excluded": [
            {
                "stock_code": row.stock_code,
                "reason": "NOT_IN_ACTIONABLE_QUANT_UNIVERSE",
            }
            for row in excluded_manual_rows
        ],
        "manual_forced_review_count": len(review_only_manual_rows),
        "manual_forced_review_codes": [
            row.stock_code for row in review_only_manual_rows
        ],
        "manual_review_policy": (
            "ALL_MANUAL_TO_FLASH_AND_PRO_REVIEW_ST_HARD_GATE_RETAINED"
        ),
        "flash_token_usage": job.token_usage,
        "flash_token_limit": flash_limit,
    }


def _run_final(session, job: PipelineJob, options: dict[str, Any]) -> dict[str, Any]:
    _require_secret("DEEPSEEK_API_KEY")
    if not options.get("confirm_budget"): raise ValueError("LLM_BUDGET_CONFIRMATION_REQUIRED")
    requested_flash_run_id = str(options.get("flash_run_id") or "").strip()
    flash = _select_flash_for_final(session, job.trade_date, requested_flash_run_id)
    if flash is None: raise ValueError("COMPLETED_FLASH_RUN_REQUIRED")
    settings = _workbench_settings(session)
    job.stage = "PRO_V3_LOCAL_RANKING"; session.commit()
    result = ProductionProV3ApplicationService(session, output_root()).run(
        flash.run_id,
        account_equity=Decimal(str(settings["validation_account_equity"])),
        available_cash=Decimal(str(settings["validation_available_cash"])),
    )
    market_review: dict[str, Any] = {"status": "NOT_RUN"}
    review_service = MarketReviewService(session)
    if bool(review_service.config.get("auto_run_after_pipeline", True)):
        try:
            review = review_service.run(job.trade_date, mode="DATA_ONLY")
            market_review = {
                "status": review["run"]["status"],
                "run_id": review["run"]["run_id"],
            }
        except Exception as exc:
            market_review = {"status": "FAILED", "error_code": type(exc).__name__}
    return {
        "run_ids": {"flash_run_id": flash.run_id, **result, "market_review_run_id": market_review.get("run_id")},
        "candidate_count": result["candidate_count"],
        "market_review": market_review,
    }


def _select_flash_for_final(
    session,
    trade_date: date,
    requested_flash_run_id: str = "",
) -> ModelValidationRun | None:
    flash_query = select(ModelValidationRun).join(
        QuantRun, QuantRun.run_id == ModelValidationRun.quant_run_id
    ).where(
        QuantRun.base_market_trade_date == trade_date,
        ModelValidationRun.real_llm.is_(True),
        ModelValidationRun.status.in_(["COMPLETED", "SUCCESS", "PARTIAL_SUCCESS"]),
    )
    if requested_flash_run_id:
        flash_query = flash_query.where(ModelValidationRun.run_id == requested_flash_run_id)
    flashes = list(session.scalars(flash_query.order_by(ModelValidationRun.created_at.desc())))
    return next((row for row in flashes if _flash_usable_for_final(row)), None)


def _run_market_review(session, job: PipelineJob, options: dict[str, Any]) -> dict[str, Any]:
    job.stage = "BUILDING_MARKET_SNAPSHOT"
    job.progress_current, job.progress_total = 1, 8
    session.commit()
    result = MarketReviewService(session).run(
        job.trade_date,
        mode=str(options.get("mode") or "DATA_ONLY"),
        force=bool(options.get("force")),
        allow_real_pro=bool(options.get("allow_real_pro", False)),
        allow_real_search=bool(options.get("allow_real_search", False)),
    )
    job.stage = "PERSISTING_RESULTS"
    job.progress_current = 7
    session.commit()
    run = result["run"]
    stats = result.get("search", {}).get("stats", {})
    return {
        "run_ids": {"market_review_run_id": run["run_id"]},
        "market_review_status": run["status"],
        "search_status": run["search_status"],
        "search_query_count": stats.get("query_count", 0),
        "search_result_count": stats.get("raw_result_count", 0),
        "evidence_count": stats.get("valid_evidence_count", 0),
        "official_evidence_count": stats.get("official_evidence_count", 0),
    }


def _run_export(session, job: PipelineJob, options: dict[str, Any]) -> dict[str, Any]:
    pro = session.scalar(select(ProResumeRun).where(
        ProResumeRun.base_trade_date == job.trade_date, ProResumeRun.status == "COMPLETED"
    ).order_by(ProResumeRun.created_at.desc()))
    if pro is None: raise ValueError("COMPLETED_PRO_RUN_REQUIRED")
    job.stage = "OPENPYXL_EXCEL_EXPORT"; session.commit()
    result = DailyExcelExportService(session, output_root()).export(job.trade_date, pro.flash_validation_run_id, pro.run_id)
    bundle = HistoricalPipelineRunResolver(session).reconcile(job.trade_date, pro.pipeline_run_id)
    return {
        "run_ids": {"pipeline_run_id": pro.pipeline_run_id, "flash_run_id": pro.flash_validation_run_id, "pro_run_id": pro.run_id},
        "output_path": result["output_path"],
        "sha256": result["sha256"],
        "sheet_count": result["sheet_count"],
        "registry_id": bundle.get("registry_id"),
    }


def _workbench_settings(session) -> dict[str, Any]:
    from backend.workbench.service import WorkbenchService
    return WorkbenchService(session).settings()


def _mark_job_success(job: PipelineJob, result: dict[str, Any]) -> None:
    job.status, job.stage = "SUCCESS", "COMPLETED"
    if int(job.progress_total or 0) <= 1:
        job.progress_current, job.progress_total = 1, 1
    else:
        job.progress_current = job.progress_total
    if int(job.success_count or 0) == 0 and int(job.failure_count or 0) == 0:
        job.success_count = 1
    job.current_stock = None
    job.run_ids = result.get("run_ids", {})
    job.output_path = result.get("output_path")
    checkpoint = dict(job.checkpoint or {})
    checkpoint["result"] = {
        key: value for key, value in result.items() if key not in {"run_ids", "output_path"}
    }
    job.checkpoint = checkpoint
    job.finished_at = datetime.now(timezone.utc)


def _flash_tokens_for_date(session, trade_date: date) -> int:
    value = session.scalar(
        select(func.coalesce(func.sum(
            ModelValidationLLMAudit.input_tokens + ModelValidationLLMAudit.output_tokens
        ), 0))
        .join(ModelValidationRun, ModelValidationRun.run_id == ModelValidationLLMAudit.validation_run_id)
        .where(ModelValidationRun.base_market_trade_date == trade_date)
    )
    return int(value or 0)


def _partition_manual_rows(
    manual_rows: list[ManualSelectionRecord],
    quant_codes: set[str],
    *,
    include_review_only: bool = False,
) -> tuple[list[ManualSelectionRecord], list[ManualSelectionRecord]]:
    canonical_quant_codes = {
        str(code or "").strip().upper().split(".", 1)[0]
        for code in quant_codes
    }
    eligible: list[ManualSelectionRecord] = []
    excluded: list[ManualSelectionRecord] = []
    for row in manual_rows:
        code = str(row.stock_code or "").strip().upper().split(".", 1)[0]
        if code in canonical_quant_codes or include_review_only:
            eligible.append(row)
        else:
            excluded.append(row)
    return eligible, excluded


def _flash_usable_for_final(run: ModelValidationRun) -> bool:
    snapshot = dict(run.config_snapshot or {})
    quality = dict(snapshot.get("flash_batch_quality") or {})
    return bool(
        quality
        and not quality.get("degenerate")
        and quality.get("usable_for_final", True)
        and not snapshot.get("reusable_source_only")
    )


def _finalize_interrupted_flash_run(session, quant_run_id: str, exc: Exception) -> None:
    run = session.scalar(select(ModelValidationRun).where(
        ModelValidationRun.quant_run_id == quant_run_id,
        ModelValidationRun.status == "RUNNING",
    ).order_by(ModelValidationRun.created_at.desc()))
    if run is None:
        return
    sample_count = int(session.scalar(
        select(func.count()).select_from(ModelValidationSample).where(
            ModelValidationSample.validation_run_id == run.run_id
        )
    ) or 0)
    run.status = "PARTIAL_SUCCESS" if sample_count else "FAILED"
    run.warnings = sorted(set([
        *list(run.warnings or []),
        f"FLASH_WORKFLOW_INTERRUPTED:{type(exc).__name__}",
    ]))
    snapshot = dict(run.config_snapshot or {})
    snapshot.update({
        "reusable_source_only": bool(sample_count),
        "usable_for_final": False,
        "interrupted_error": type(exc).__name__,
        "completed_sample_count": sample_count,
    })
    run.config_snapshot = snapshot
    session.commit()


def _require_secret(name: str) -> None:
    if not os.getenv(name, "").strip(): raise ValueError(f"{name}_NOT_CONFIGURED")


def _safe_options(options: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "force", "confirm_budget", "data_fetch_workers", "resume_from_job_id",
        "mode", "allow_real_pro", "allow_real_search", "quant_run_id",
        "manual_hash", "flash_run_id", "contract_version", "recompute",
    }
    if set(options) - allowed: raise ValueError("JOB_OPTION_NOT_ALLOWED")
    return {key: options[key] for key in sorted(options)}


def _safe_actor(actor: dict[str, Any] | None) -> dict[str, str]:
    value = actor or {}
    email = str(value.get("email") or "local_trader").strip().lower()[:320]
    role = str(value.get("role") or "TRADER").strip().upper()
    return {"email": email, "role": role if role in {"ADMIN", "TRADER", "VIEWER"} else "TRADER"}


def _release_job_lock(session, job_id: str) -> None:
    session.execute(delete(JobExecutionLock).where(JobExecutionLock.job_id == job_id))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _safe_error(exc: Exception) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ")[:500]
    for value in (os.getenv("TUSHARE_TOKEN", ""), os.getenv("DEEPSEEK_API_KEY", ""), os.getenv("OPENAI_API_KEY", "")):
        if value: text = text.replace(value, "[REDACTED]")
    return text


def _check_cancelled(session, job: PipelineJob) -> None:
    session.refresh(job)
    if job.status == "CANCELLATION_REQUESTED": raise JobCancelled()


def _job_payload(row: PipelineJob) -> dict[str, Any]:
    payload = {key: getattr(row, key) for key in (
        "job_id", "job_type", "trade_date", "status", "stage", "progress_current", "progress_total",
        "current_stock", "success_count", "failure_count", "token_usage", "cost_usd", "started_at",
        "finished_at", "error_code", "error_message", "run_ids", "output_path", "checkpoint",
    )}
    checkpoint = row.checkpoint or {}
    payload["started_by"] = checkpoint.get("started_by")
    payload["started_by_role"] = checkpoint.get("started_by_role")
    return payload


class JobCancelled(RuntimeError):
    pass
