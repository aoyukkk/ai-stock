from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import BaseModel, ConfigDict

from backend.application.workflow import WorkflowApplicationService, execute_persisted_job
from backend.core.responses import error_response, success_response
from database.session import get_session, init_db
from market_review.repository import MarketReviewRepository
from market_review.service import MarketReviewService


router = APIRouter(prefix="/api/workbench/market-review", tags=["market-daily-review"])


class MarketReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    mode: Literal["FULL", "DATA_ONLY", "REFRESH_EVIDENCE", "REGENERATE_SUMMARY"] = "DATA_ONLY"
    force: bool = False
    allow_real_pro: bool = False
    allow_real_search: bool = False


def _session():
    init_db()
    return get_session()


def _start(body: MarketReviewRequest, background_tasks: BackgroundTasks, request: Request | None = None) -> dict[str, Any]:
    session = _session()
    try:
        result = WorkflowApplicationService(session).start("MARKET_DAILY_REVIEW", body.trade_date, {
            "mode": body.mode, "force": body.force, "allow_real_pro": body.allow_real_pro,
            "allow_real_search": body.allow_real_search,
        }, actor=_actor(request))
        if result.get("duplicate_status") == "NEW_JOB":
            background_tasks.add_task(execute_persisted_job, result["job_id"])
        return result
    finally:
        session.close()


@router.post("/run")
def run_review(body: MarketReviewRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    try:
        return success_response(data=_start(body, background_tasks, request), trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("MARKET_REVIEW_JOB_BLOCKED", str(exc), trace_id=request.state.trace_id)


@router.get("/latest")
def latest(request: Request, trade_date: date | None = None, run_id: str | None = None) -> dict:
    session = _session()
    try:
        data = MarketReviewService(session).latest(trade_date, run_id)
        return success_response(data=data or {}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/history")
def history(request: Request, trade_date: date | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict:
    session = _session()
    try:
        items = MarketReviewService(session).history(trade_date)
        start = (page - 1) * page_size
        payload = {"items": items[start:start + page_size], "total": len(items), "page": page, "page_size": page_size, "total_pages": (len(items) + page_size - 1) // page_size if items else 0}
        return success_response(data=payload, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/snapshot")
def snapshot(trade_date: date, request: Request) -> dict:
    return _bundle_part(trade_date, request, "snapshot")


@router.get("/drivers")
def drivers(trade_date: date, request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict:
    return _paged_part(trade_date, request, "drivers", page, page_size)


@router.get("/evidence")
def evidence(trade_date: date, request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict:
    return _paged_part(trade_date, request, "evidence", page, page_size)


@router.get("/outlook")
def outlook(trade_date: date, request: Request) -> dict:
    session = _session()
    try:
        bundle = MarketReviewService(session).latest(trade_date) or {}
        return success_response(data={"outlook": bundle.get("outlook") or {}, "scenarios": bundle.get("scenarios") or []}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/evidence/refresh")
def refresh_evidence(body: MarketReviewRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    body.mode = "REFRESH_EVIDENCE"
    return run_review(body, request, background_tasks)


@router.post("/summary/regenerate")
def regenerate_summary(body: MarketReviewRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    body.mode = "REGENERATE_SUMMARY"
    return run_review(body, request, background_tasks)


@router.post("/export")
def export(body: MarketReviewRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    session = _session()
    try:
        result = WorkflowApplicationService(session).start(
            "EXPORT", body.trade_date, {"force": body.force}, actor=_actor(request)
        )
        if result.get("duplicate_status") == "NEW_JOB":
            background_tasks.add_task(execute_persisted_job, result["job_id"])
        return success_response(data=result, trace_id=request.state.trace_id)
    except ValueError as exc:
        return error_response("MARKET_REVIEW_EXPORT_BLOCKED", str(exc), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/methodology")
def methodology(request: Request) -> dict:
    return success_response(data={
        "snapshot_version": "market_daily_snapshot_v1",
        "regime_version": "market_regime_v1",
        "outlook_version": "market_outlook_rule_v1",
        "wire_contract": "market_daily_review_wire_v1",
        "probability_owner": "DETERMINISTIC_RULE_ENGINE",
        "pro_scope": "SUMMARY_ONLY",
        "automatic_scheduler": False,
        "real_trading": False,
    }, trace_id=request.state.trace_id)


def _actor(request: Request | None) -> dict[str, str] | None:
    user = getattr(request.state, "internal_user", None) if request else None
    return {"email": user.email, "role": user.role} if user else None


def _bundle_part(trade_date: date, request: Request, key: str) -> dict:
    session = _session()
    try:
        bundle = MarketReviewService(session).latest(trade_date) or {}
        return success_response(data=bundle.get(key) or {}, trace_id=request.state.trace_id)
    finally:
        session.close()


def _paged_part(trade_date: date, request: Request, key: str, page: int, page_size: int) -> dict:
    session = _session()
    try:
        bundle = MarketReviewService(session).latest(trade_date) or {}
        items = list(bundle.get(key) or [])
        start = (page - 1) * page_size
        payload = {"items": items[start:start + page_size], "total": len(items), "page": page, "page_size": page_size, "total_pages": (len(items) + page_size - 1) // page_size if items else 0}
        return success_response(data=payload, trace_id=request.state.trace_id)
    finally:
        session.close()
