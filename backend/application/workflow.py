from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from backend.application.excel_export import DailyExcelExportService
from backend.application.pro_v3 import ProductionProV3ApplicationService
from backend.core.runtime_paths import output_root, report_root, tushare_cache_root
from backend.workbench.historical import HistoricalPipelineRunResolver
from database.models.quant_run import QuantRun
from database.models.system import LLMUsage
from database.models.validation import (
    ModelValidationLLMAudit,
    ModelValidationRun,
    ModelValidationSample,
    ProResumeRun,
)
from database.models.workbench import ManualSelectionRecord, PipelineJob
from database.session import get_session, init_db
from database.stock_master_sync import StockMasterSyncService
from datasource.tushare_provider import TushareMarketDataProvider
from quant.run_repository import QuantRunRepository
from research.knowledge_mode import LLMKnowledgeMode
from scripts.prewarm_tushare_trade_date_cache import run_prewarm
from scripts.run_real_quant_top500 import run_real_quant_top500
from temporal.readiness import DataReadinessService
from temporal.schemas import RunMode, TemporalStatus
from trader_demo.runtime import temporary_real_llm_runtime
from trader_demo.budget import PipelineBudgetConfig
from trader_demo.service import ManualSelection, TraderDemoService


ACTIVE = {"PENDING", "RUNNING", "CANCELLATION_REQUESTED"}
JOB_TYPES = {"DATA", "QUANT", "FLASH", "FINAL", "EXPORT"}


class WorkflowApplicationService:
    """Persistent application service shared by HTTP and desktop entry points."""

    def __init__(self, session) -> None:
        self.session = session

    def start(self, job_type: str, trade_date: date, options: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = job_type.upper()
        if normalized not in JOB_TYPES:
            raise ValueError("INVALID_JOB_TYPE")
        options = _safe_options(options or {})
        request_hash = hashlib.sha256(json.dumps({"job_type": normalized, "trade_date": str(trade_date), "options": options}, sort_keys=True).encode()).hexdigest()
        active = self.session.scalar(select(PipelineJob).where(
            PipelineJob.job_type == normalized,
            PipelineJob.trade_date == trade_date,
            PipelineJob.status.in_(ACTIVE),
        ).order_by(PipelineJob.created_at.desc()))
        if active is not None:
            return _job_payload(active) | {"duplicate_status": "ACTIVE_JOB_REUSED"}
        completed = self.session.scalar(select(PipelineJob).where(
            PipelineJob.job_type == normalized,
            PipelineJob.trade_date == trade_date,
            PipelineJob.status == "SUCCESS",
        ).order_by(PipelineJob.created_at.desc()))
        if completed is not None and (completed.checkpoint or {}).get("request_hash") == request_hash and not options.get("force"):
            return _job_payload(completed) | {"duplicate_status": "SUCCESS_CACHE_HIT"}
        row = PipelineJob(
            job_id=f"desktop-{uuid.uuid4().hex[:20]}", job_type=normalized, trade_date=trade_date,
            status="PENDING", stage="QUEUED", progress_current=0, progress_total=1,
            success_count=0, failure_count=0, token_usage=0, cost_usd=0.0,
            run_ids={}, checkpoint={"request_hash": request_hash, "options": options, "mode": "REAL_APPLICATION_SERVICE"},
        )
        self.session.add(row)
        self.session.commit()
        return _job_payload(row) | {"duplicate_status": "NEW_JOB"}

    def cancel(self, job_id: str) -> dict[str, Any]:
        row = self._job(job_id)
        if row.status in {"PENDING", "RUNNING"}:
            row.status = "CANCELLATION_REQUESTED"
            row.stage = "CANCELLATION_REQUESTED"
            self.session.commit()
        return _job_payload(row)

    def resume(self, job_id: str) -> dict[str, Any]:
        row = self._job(job_id)
        if row.status not in {"FAILED", "CANCELLED", "PARTIAL_SUCCESS"}:
            raise ValueError("JOB_NOT_RESUMABLE")
        options = dict((row.checkpoint or {}).get("options") or {}) | {"resume_from_job_id": row.job_id, "force": True}
        return self.start(row.job_type, row.trade_date, options)

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
            session.commit(); return
        job.status, job.stage, job.started_at = "RUNNING", "STARTING", datetime.now(timezone.utc)
        session.commit()
        options = dict((job.checkpoint or {}).get("options") or {})
        handlers = {
            "DATA": _run_data,
            "QUANT": _run_quant,
            "FLASH": _run_flash,
            "FINAL": _run_final,
            "EXPORT": _run_export,
        }
        result = handlers[job.job_type](session, job, options)
        _check_cancelled(session, job)
        _mark_job_success(job, result)
        session.commit()
    except JobCancelled:
        job.status, job.stage, job.finished_at = "CANCELLED", "CANCELLED", datetime.now(timezone.utc)
        session.commit()
    except Exception as exc:
        job.status, job.stage = "FAILED", "FAILED"
        job.failure_count = max(int(job.failure_count or 0), 1)
        job.error_code = type(exc).__name__[:128]
        job.error_message = _safe_error(exc)
        job.finished_at = datetime.now(timezone.utc)
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
    after_llm = int(session.scalar(select(LLMUsage.id).order_by(LLMUsage.id.desc())) or 0)
    if before_llm != after_llm or not row.no_llm_call_verified:
        raise RuntimeError("QUANT_ZERO_LLM_ASSERTION_FAILED")
    return {"run_ids": {"quant_run_id": row.run_id, "manifest_id": manifest.id}, "scored_count": row.scored_count, "no_llm_call_verified": True}


def _run_flash(session, job: PipelineJob, options: dict[str, Any]) -> dict[str, Any]:
    _require_secret("DEEPSEEK_API_KEY")
    if not options.get("confirm_budget"):
        raise ValueError("LLM_BUDGET_CONFIRMATION_REQUIRED")
    quant = session.scalar(select(QuantRun).where(
        QuantRun.base_market_trade_date == job.trade_date,
        QuantRun.status == "COMPLETED", QuantRun.actionable.is_(True), QuantRun.no_llm_call_verified.is_(True),
    ).order_by(QuantRun.created_at.desc()))
    if quant is None: raise ValueError("ACTIONABLE_QUANT_RUN_REQUIRED")
    manual_rows = list(session.scalars(select(ManualSelectionRecord).where(ManualSelectionRecord.trade_date == job.trade_date)))
    manual = [ManualSelection(row.stock_code, row.reason, row.priority) for row in manual_rows]
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
                knowledge_mode=LLMKnowledgeMode.LLM_UNVERIFIED_CURRENT,
                model_validation_top_n=int(settings["llm_top_n"]), defer_candidate_generation=True,
                concurrency=int(settings["flash_concurrency"]), batch_size=int(settings["flash_batch_size"]),
                checkpoint_callback=checkpoint,
            )
    except Exception as exc:
        _finalize_interrupted_flash_run(session, quant.run_id, exc)
        raise
    return {
        "run_ids": {"quant_run_id": quant.run_id, "flash_run_id": validation_run_id},
        "manual_count": len(manual), "flash_token_usage": job.token_usage,
        "flash_token_limit": flash_limit,
    }


def _run_final(session, job: PipelineJob, options: dict[str, Any]) -> dict[str, Any]:
    _require_secret("DEEPSEEK_API_KEY")
    if not options.get("confirm_budget"): raise ValueError("LLM_BUDGET_CONFIRMATION_REQUIRED")
    flashes = list(session.scalars(select(ModelValidationRun).join(QuantRun, QuantRun.run_id == ModelValidationRun.quant_run_id).where(
        QuantRun.base_market_trade_date == job.trade_date,
        ModelValidationRun.real_llm.is_(True),
        ModelValidationRun.status.in_(["COMPLETED", "SUCCESS", "PARTIAL_SUCCESS"]),
    ).order_by(ModelValidationRun.created_at.desc())))
    flash = next((row for row in flashes if _flash_usable_for_final(row)), None)
    if flash is None: raise ValueError("COMPLETED_FLASH_RUN_REQUIRED")
    settings = _workbench_settings(session)
    job.stage = "PRO_V3_LOCAL_RANKING"; session.commit()
    result = ProductionProV3ApplicationService(session, output_root()).run(
        flash.run_id,
        account_equity=Decimal(str(settings["validation_account_equity"])),
        available_cash=Decimal(str(settings["validation_available_cash"])),
    )
    return {"run_ids": {"flash_run_id": flash.run_id, **result}, "candidate_count": result["candidate_count"]}


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
        "sheet_count": 5,
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
    allowed = {"force", "confirm_budget", "data_fetch_workers", "resume_from_job_id"}
    if set(options) - allowed: raise ValueError("JOB_OPTION_NOT_ALLOWED")
    return {key: options[key] for key in sorted(options)}


def _safe_error(exc: Exception) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ")[:500]
    for value in (os.getenv("TUSHARE_TOKEN", ""), os.getenv("DEEPSEEK_API_KEY", ""), os.getenv("OPENAI_API_KEY", "")):
        if value: text = text.replace(value, "[REDACTED]")
    return text


def _check_cancelled(session, job: PipelineJob) -> None:
    session.refresh(job)
    if job.status == "CANCELLATION_REQUESTED": raise JobCancelled()


def _job_payload(row: PipelineJob) -> dict[str, Any]:
    return {key: getattr(row, key) for key in (
        "job_id", "job_type", "trade_date", "status", "stage", "progress_current", "progress_total",
        "current_stock", "success_count", "failure_count", "token_usage", "cost_usd", "started_at",
        "finished_at", "error_code", "error_message", "run_ids", "output_path", "checkpoint",
    )}


class JobCancelled(RuntimeError):
    pass
