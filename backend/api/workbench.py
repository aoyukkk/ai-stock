from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.core.responses import error_response, success_response
from backend.workbench.service import WorkbenchService
from database.session import get_session, init_db
from review.performance_schemas import PerformanceRequest
from review.selection_performance_service import SelectionPerformanceService


router = APIRouter(prefix="/api/workbench", tags=["trader-daily-workbench"])


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    mode: Literal["USE_EXISTING", "MOCK"] = "USE_EXISTING"


class ManualSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    stock_code: str = Field(min_length=1, max_length=32)
    reason: str = Field(default="", max_length=500)
    priority: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    quant_run_id: str | None = Field(default=None, max_length=64)


class ManualSelectionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)
    priority: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"


class ManualSelectionBatchRequest(ManualSelectionRequest):
    stock_codes: list[str] = Field(min_length=1, max_length=100)
    stock_code: str = "BATCH"


class DataCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date


class DataUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    mode: Literal["USE_EXISTING", "MOCK", "MISSING_ONLY", "FORCE_REFRESH"] = "USE_EXISTING"


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any]


class ExistingRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    pipeline_run_id: str | None = Field(default=None, max_length=128)


class SecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=8, max_length=512)


def _service() -> tuple[Any, WorkbenchService]:
    init_db()
    session = get_session()
    return session, WorkbenchService(session)


def _execute_performance(run_id: str, job_id: str) -> None:
    init_db()
    session = get_session()
    try:
        SelectionPerformanceService(session).execute(run_id, job_id)
    finally:
        session.close()


@router.get("/available-dates")
def available_dates(request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data={"items": service.available_dates()}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/runs")
def available_runs(trade_date: date, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data={"items": service.available_runs(trade_date)}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/runs/reconcile")
def reconcile_run(body: ExistingRunRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.reconcile(body.trade_date, body.pipeline_run_id), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/runs/load-existing")
def load_existing_run(body: ExistingRunRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.reconcile(body.trade_date, body.pipeline_run_id), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/status")
def status(trade_date: date, request: Request, pipeline_run_id: str | None = None) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.status(trade_date, pipeline_run_id), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/data/check")
def data_check(body: DataCheckRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.data_check(body.trade_date), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/data/update")
def data_update(body: DataUpdateRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    session, service = _service()
    try:
        if body.mode in {"MISSING_ONLY", "FORCE_REFRESH"}:
            return error_response(
                "DATA_PROVIDER_NOT_ENABLED",
                "当前工作台仅允许使用本地已有数据或 Mock，不会在此接口请求外部数据源。",
                trace_id=request.state.trace_id,
            )
        result = service.start_job("DATA", body.trade_date, mode=body.mode)
        performance = SelectionPerformanceService(session)
        settings = performance.settings()
        if settings["auto_refresh_after_data_ready"]:
            started = performance.start(PerformanceRequest(
                evaluation_end_date=body.trade_date,
                lookback_value=int(settings["default_lookback_value"]),
                return_basis=str(settings["default_return_basis"]),
                selection_scope=str(settings["default_selection_scope"]),
                weighting_mode=str(settings["default_weighting_mode"]),
                include_zero_position_stocks=bool(settings["include_zero_position_stocks"]),
                include_risk_blocked_stocks=bool(settings["include_risk_blocked_stocks"]),
            ))
            if started.get("job_id") and started.get("performance_run_id"):
                background_tasks.add_task(_execute_performance, started["performance_run_id"], started["job_id"])
            result["selection_performance_refresh"] = started
        return success_response(data=result, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/settings")
def settings(request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.settings(), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.put("/settings")
def update_settings(body: SettingsRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.update_settings(body.values), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("WORKBENCH_SETTINGS_INVALID", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/secrets/status")
def secret_status(request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.secret_status(), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/secrets/{provider}")
def set_secret(provider: str, body: SecretRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.set_secret(provider, body.value), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("SECRET_UPDATE_INVALID", str(exc), trace_id=request.state.trace_id)
    finally:
        body.value = ""
        session.close()


@router.post("/secrets/{provider}/test")
def test_secret(provider: str, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.test_secret(provider), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("SECRET_PROVIDER_INVALID", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.delete("/secrets/{provider}")
def delete_secret(provider: str, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.delete_secret(provider), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("SECRET_PROVIDER_INVALID", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/quant/results")
def quant_results(trade_date: date, request: Request, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), keyword: str = "", only_top: bool = False, only_manual: bool = False, only_candidate: bool = False, quant_run_id: str | None = None, pipeline_run_id: str | None = None, sort_by: str = "rank", sort_order: str = "asc") -> dict:
    session, service = _service()
    try:
        return success_response(data=service.list_quant(trade_date, page=page, page_size=page_size, keyword=keyword, only_top=only_top, only_manual=only_manual, only_candidate=only_candidate, quant_run_id=quant_run_id, pipeline_run_id=pipeline_run_id, sort_by=sort_by, sort_order=sort_order), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/flash/results")
def flash_results(trade_date: date, request: Request, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), flash_run_id: str | None = None, pipeline_run_id: str | None = None, keyword: str = "", sort_by: str = "rank", sort_order: Literal["asc", "desc"] = "asc") -> dict:
    session, service = _service()
    try:
        return success_response(data=service.list_flash(trade_date, page=page, page_size=page_size, flash_run_id=flash_run_id, pipeline_run_id=pipeline_run_id, keyword=keyword, sort_by=sort_by, sort_order=sort_order), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/final/results")
def final_results(trade_date: date, request: Request, pipeline_run_id: str | None = None) -> dict:
    session, service = _service()
    try:
        return success_response(data={"items": service.final_results(trade_date, pipeline_run_id)}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/order-position/results")
def order_position_results(trade_date: date, request: Request, pipeline_run_id: str | None = None) -> dict:
    session, service = _service()
    try:
        return success_response(data={"items": service.order_position_results(trade_date, pipeline_run_id)}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/fundamentals/results")
def fundamentals(trade_date: date, request: Request, pipeline_run_id: str | None = None) -> dict:
    session, service = _service()
    try:
        return success_response(data={"items": service.fundamentals(trade_date, pipeline_run_id)}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/manual-selections")
def manual_selections(trade_date: date, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data={"items": service.manual_selections(trade_date)}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/manual-selections/snapshot")
def manual_selection_snapshot(trade_date: date, request: Request, pipeline_run_id: str | None = None) -> dict:
    session, service = _service()
    try:
        return success_response(data={"items": service.manual_snapshot(trade_date, pipeline_run_id)}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/manual-selections")
def add_manual_selection(body: ManualSelectionRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.add_manual(body.trade_date, body.stock_code, body.reason, body.priority, body.quant_run_id), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("MANUAL_SELECTION_INVALID", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.put("/manual-selections/{selection_id}")
def update_manual_selection(selection_id: int, body: ManualSelectionUpdateRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.update_manual(selection_id, body.reason, body.priority), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("MANUAL_SELECTION_NOT_FOUND", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/manual-selections/batch")
def add_manual_selection_batch(body: ManualSelectionBatchRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.add_manual_batch(body.trade_date, body.stock_codes, body.reason, body.priority, body.quant_run_id), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("MANUAL_SELECTION_INVALID", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.delete("/manual-selections/{selection_id}")
def delete_manual_selection(selection_id: int, request: Request) -> dict:
    session, service = _service()
    try:
        service.delete_manual(selection_id)
        return success_response(data={"deleted": True}, trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("MANUAL_SELECTION_NOT_FOUND", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.delete("/manual-selections")
def clear_manual_selections(trade_date: date, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data={"deleted": service.clear_manual(trade_date)}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/{job_type}/run")
def start_job(job_type: Literal["data", "quant", "flash", "final", "export"], body: JobRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.start_job(job_type.upper(), body.trade_date, mode=body.mode), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("WORKBENCH_JOB_BLOCKED", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/export/excel")
def export_excel(body: JobRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.start_job("EXPORT", body.trade_date, mode=body.mode), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("WORKBENCH_JOB_BLOCKED", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/jobs")
def jobs(trade_date: date, request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200)) -> dict:
    session, service = _service()
    try:
        items = service.jobs(trade_date)
        total = len(items)
        start = (page - 1) * page_size
        return success_response(data={"items": items[start:start + page_size], "total": total, "page": 1 if total == 0 else page, "page_size": page_size, "total_pages": (total + page_size - 1) // page_size if total else 0}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/jobs/{job_id}")
def job(job_id: str, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.job(job_id), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("JOB_NOT_FOUND", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.cancel_job(job_id), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("JOB_NOT_FOUND", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.resume_job(job_id), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("JOB_NOT_RESUMABLE", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()
