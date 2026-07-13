from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.core.responses import error_response, success_response
from database.session import get_session, init_db
from review.performance_schemas import PerformanceRequest
from review.performance_excel import PerformanceExcelExporter
from review.selection_performance_service import SelectionPerformanceService


router = APIRouter(prefix="/api/workbench/performance", tags=["selection-performance"])


class PerformanceRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_end_date: date
    lookback_value: int = Field(default=5, ge=1, le=120)
    lookback_unit: Literal["TRADING_DAYS", "CUSTOM"] = "TRADING_DAYS"
    start_selection_date: date | None = None
    end_selection_date: date | None = None
    return_basis: Literal["NEXT_OPEN", "SIGNAL_CLOSE"] = "NEXT_OPEN"
    selection_scope: Literal["FINAL_CANDIDATES", "LLM_ONLY", "MANUAL_ONLY", "BOTH_ONLY", "NON_ZERO_POSITION", "ALL_CANDIDATES_INCLUDING_ZERO_POSITION"] = "FINAL_CANDIDATES"
    weighting_mode: Literal["EQUAL_WEIGHT", "SUGGESTED_POSITION_WEIGHT"] = "EQUAL_WEIGHT"
    include_zero_position_stocks: bool = True
    include_risk_blocked_stocks: bool = True
    force_recalculate: bool = False


class IncrementalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    performance_run_id: str
    evaluation_end_date: date


class InvalidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    performance_run_id: str
    reason: str = Field(min_length=1, max_length=500)


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any]


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    performance_run_id: str


def _service():
    init_db()
    session = get_session()
    return session, SelectionPerformanceService(session)


def _execute(run_id: str, job_id: str) -> None:
    session, service = _service()
    try:
        service.execute(run_id, job_id)
    finally:
        session.close()


def _execute_incremental(run_id: str, job_id: str, evaluation_end_date: date) -> None:
    session, service = _service()
    try:
        service.execute_incremental(run_id, job_id, evaluation_end_date)
    finally:
        session.close()


@router.post("/run")
def start(body: PerformanceRunRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    session, service = _service()
    try:
        result = service.start(PerformanceRequest(**body.model_dump()))
        if result.get("job_id") and result.get("performance_run_id"):
            background_tasks.add_task(_execute, result["performance_run_id"], result["job_id"])
        return success_response(data=result, trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("PERFORMANCE_REQUEST_INVALID", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/summary")
def summary(request: Request, performance_run_id: str | None = None) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.summary(performance_run_id), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/cohorts")
def cohorts(request: Request, performance_run_id: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), sort_by: str = "selection_trade_date", sort_order: Literal["asc", "desc"] = "desc") -> dict:
    session, service = _service()
    try:
        return success_response(data=_page(_sort(service.cohort_summary(performance_run_id), sort_by, sort_order), page, page_size), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/daily")
def daily(request: Request, performance_run_id: str | None = None, selection_date: date | None = None, evaluation_date: date | None = None, status: str = "", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), sort_by: str = "evaluation_trade_date", sort_order: Literal["asc", "desc"] = "asc") -> dict:
    session, service = _service()
    try:
        rows = service.portfolio_daily(performance_run_id)
        rows = [row for row in rows if (not selection_date or row["selection_trade_date"] == selection_date) and (not evaluation_date or row["evaluation_trade_date"] == evaluation_date) and (not status or row["status"] == status)]
        return success_response(data=_page(_sort(rows, sort_by, sort_order), page, page_size), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/stocks")
def stocks(request: Request, performance_run_id: str | None = None, selection_date: date | None = None, evaluation_date: date | None = None, stock_code: str = "", keyword: str = "", source: str = "", sign: Literal["", "POSITIVE", "NEGATIVE"] = "", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), sort_by: str = "evaluation_trade_date", sort_order: Literal["asc", "desc"] = "asc") -> dict:
    session, service = _service()
    try:
        rows = service.stock_daily(performance_run_id)
        needle = (stock_code or keyword).lower()
        rows = [row for row in rows if (not selection_date or row["selection_trade_date"] == selection_date) and (not evaluation_date or row["evaluation_trade_date"] == evaluation_date) and (not source or row["selection_source"] == source) and (not needle or needle in row["stock_code"].lower() or needle in row["stock_name"].lower()) and (not sign or (row["cumulative_return"] is not None and ((sign == "POSITIVE" and row["cumulative_return"] > 0) or (sign == "NEGATIVE" and row["cumulative_return"] < 0))))]
        return success_response(data=_page(_sort(rows, sort_by, sort_order), page, page_size), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/runs")
def runs(request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data={"items": service.runs()}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/cache-status")
def cache_status(request: Request, performance_run_id: str | None = None) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.cache_status(performance_run_id), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/incremental-refresh")
def incremental(body: IncrementalRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    session, service = _service()
    try:
        result = service.incremental_refresh(body.performance_run_id, body.evaluation_end_date)
        if result.get("job_id") and result.get("performance_run_id") and result.get("status") == "PENDING":
            background_tasks.add_task(_execute_incremental, result["performance_run_id"], result["job_id"], body.evaluation_end_date)
        return success_response(data=result, trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("PERFORMANCE_INCREMENTAL_INVALID", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/invalidate")
def invalidate(body: InvalidateRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.invalidate(body.performance_run_id, body.reason), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("PERFORMANCE_INVALIDATION_FAILED", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/methodology")
def methodology(request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.methodology(), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/export")
def export_excel(body: ExportRequest, request: Request) -> dict:
    session, service = _service()
    try:
        detail = service.run_detail(body.performance_run_id)
        output = Path("outputs") / detail["evaluation_end_date"].isoformat() / f"selection_performance_{detail['evaluation_end_date'].isoformat()}_{detail['lookback_value']}td.xlsx"
        payload = {"cohorts": service.cohort_summary(body.performance_run_id), "daily": service.portfolio_daily(body.performance_run_id), "stocks": service.stock_daily(body.performance_run_id), "methodology": service.methodology() | {"run": detail}}
        return success_response(data=PerformanceExcelExporter().export(output, payload), trace_id=request.state.trace_id)
    except (ValueError, RuntimeError) as exc:
        return error_response("PERFORMANCE_EXPORT_FAILED", str(exc), trace_id=request.state.trace_id)
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
        return error_response("PERFORMANCE_SETTINGS_INVALID", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


def _page(rows: list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
    start = (page - 1) * page_size
    total = len(rows)
    return {"items": rows[start:start + page_size], "total": total, "page": 1 if total == 0 else page, "page_size": page_size, "total_pages": (total + page_size - 1) // page_size if total else 0}


def _sort(rows: list[dict[str, Any]], key: str, order: str) -> list[dict[str, Any]]:
    allowed = set(rows[0]) if rows else set()
    actual = key if key in allowed else next(iter(allowed), "")
    return sorted(rows, key=lambda row: (row.get(actual) is None, row.get(actual)), reverse=order == "desc") if actual else rows
