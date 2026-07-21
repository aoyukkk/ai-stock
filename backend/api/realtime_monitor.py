from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.core.config import get_app_config
from backend.core.responses import error_response, success_response
from database.session import get_session, init_db
from services.ifind_shadow_service import IFindShadowService, RealtimeMonitorPoolResolver
from database.models.ifind_acceptance import IFindShadowAcceptanceRun
from services.ifind_shadow_acceptance_service import IFindShadowAcceptanceService
from scripts.run_ifind_shadow_acceptance import resolve_trade_date, tushare_daily_ready


router = APIRouter(tags=["ifind-shadow-realtime"])


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["ALL", "INDEX", "STOCKS"] = "ALL"
    stock_codes: list[str] = Field(default_factory=list, max_length=100)
    force: bool = False


class TailShadowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock_code: str = Field(min_length=1, max_length=32)


class AcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_real_ifind: bool = False
    trade_date: date | None = None
    stock_limit: int = Field(default=20, ge=1, le=20)
    minute_stock_count: int = Field(default=3, ge=1, le=3)
    rounds: int = Field(default=3, ge=1, le=3)
    interval_seconds: int = Field(default=60, ge=60, le=3600)
    max_external_calls: int = Field(default=20, ge=1, le=40)
    force_provider_refresh: bool = False


def _service() -> tuple[object, IFindShadowService]:
    init_db()
    session = get_session()
    return session, IFindShadowService(session, config=get_app_config())


@router.get("/api/workbench/realtime/provider-status")
def provider_status(request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.provider_status(), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/realtime/pool")
def pool(trade_date: date, request: Request, pipeline_run_id: str | None = None) -> dict:
    session, _service_instance = _service()
    try:
        data = RealtimeMonitorPoolResolver(session).resolve(trade_date)
        data["pipeline_run_id"] = pipeline_run_id
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/api/workbench/realtime/refresh")
def refresh(body: RefreshRequest, request: Request, trade_date: date | None = None) -> dict:
    session, service = _service()
    try:
        target_date = trade_date or date.today()
        pool_data = RealtimeMonitorPoolResolver(session).resolve(target_date)
        codes = body.stock_codes or [item["stock_code"] for item in pool_data["items"]]
        if body.scope == "INDEX":
            data = service.refresh_indices(target_date)
        elif body.scope == "STOCKS":
            data = service.refresh_stocks(codes, force=body.force)
        else:
            data = service.refresh_stocks(codes, force=body.force)
            data["index"] = service.refresh_indices(target_date)
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/realtime/results")
def results(request: Request, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), keyword: str = "") -> dict:
    session, service = _service()
    try:
        return success_response(data=service.list_results(page=page, page_size=page_size, keyword=keyword), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/realtime/indices")
def indices(request: Request, trade_date: date | None = None) -> dict:
    session, service = _service()
    try:
        return success_response(data={"items": service.list_indices(trade_date)}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/realtime/minute-bars")
def minute_bars(stock_code: str, request: Request, trade_date: date | None = None,
                range_name: Literal["LAST_30M", "FULL_DAY", "CUSTOM"] = Query("LAST_30M", alias="range"),
                start: str = "09:30:00", end: str = "15:00:00", interval: str = "1m") -> dict:
    session, service = _service()
    try:
        data = service.load_minute(stock_code, start, end)
        if trade_date:
            data["items"] = service.minute_bars(stock_code, trade_date=trade_date)
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/api/workbench/realtime/tail-shadow")
def tail_shadow(body: TailShadowRequest, request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.tail_shadow(body.stock_code), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/data-sources/ifind/usage")
def usage(request: Request) -> dict:
    session, service = _service()
    try:
        return success_response(data=service.usage(), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/realtime/acceptance/latest")
def acceptance_latest(request: Request) -> dict:
    session, _service_instance = _service()
    try:
        row = session.scalars(select(IFindShadowAcceptanceRun).order_by(IFindShadowAcceptanceRun.started_at.desc()).limit(1)).first()
        return success_response(data=_acceptance_dict(row), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/realtime/acceptance/report/{acceptance_run_id}")
def acceptance_report(acceptance_run_id: str, request: Request) -> dict:
    session, _service_instance = _service()
    try:
        row = session.scalar(select(IFindShadowAcceptanceRun).where(IFindShadowAcceptanceRun.acceptance_run_id == acceptance_run_id))
        if row is None or not row.report_path:
            return error_response("ACCEPTANCE_REPORT_NOT_FOUND", "Acceptance report not found", trace_id=request.state.trace_id)
        path = (Path.cwd() / row.report_path).resolve()
        root = Path.cwd().resolve()
        if root not in path.parents or path.suffix.lower() != ".json":
            return error_response("ACCEPTANCE_REPORT_PATH_INVALID", "Acceptance report path is invalid", trace_id=request.state.trace_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return error_response("ACCEPTANCE_REPORT_READ_FAILED", "Acceptance report cannot be read", trace_id=request.state.trace_id)
        return success_response(data=payload, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/api/workbench/realtime/acceptance/{mode}")
def run_acceptance(mode: Literal["closed-session", "open-session"], body: AcceptanceRequest, request: Request) -> dict:
    if not body.confirm_real_ifind:
        return error_response("REAL_IFIND_CONFIRMATION_REQUIRED", "本操作将调用 iFinD 真实接口，请先确认只读 Shadow 验收。", trace_id=request.state.trace_id)
    session, _service_instance = _service()
    previous = {key: os.environ.get(key) for key in ("IFIND_HTTP_ENABLED", "RUN_REAL_IFIND_SHADOW")}
    try:
        os.environ["IFIND_HTTP_ENABLED"] = "true"
        os.environ["RUN_REAL_IFIND_SHADOW"] = "true"
        app_config = get_app_config()
        target = body.trade_date or resolve_trade_date("latest-completed", app_config.root_dir)
        if not tushare_daily_ready(target, app_config.root_dir):
            return error_response("TUSHARE_DAILY_DATA_NOT_READY", "Tushare 日线数据尚未完成。", trace_id=request.state.trace_id)
        result = IFindShadowAcceptanceService(session, app_config=app_config).run(mode=mode, trade_date=target, stock_limit=body.stock_limit, minute_stock_count=body.minute_stock_count, rounds=body.rounds, interval_seconds=body.interval_seconds, max_external_calls=body.max_external_calls, force_provider_refresh=body.force_provider_refresh)
        return success_response(data={"status": result.get("status"), "acceptance_run_id": result.get("acceptance_run_id"), "report_path": result.get("report_path"), "external_call_count": result.get("external_call_count", 0)}, trace_id=request.state.trace_id)
    finally:
        for key, value in previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        session.close()


def _acceptance_dict(row: IFindShadowAcceptanceRun | None) -> dict | None:
    if row is None: return None
    return {"acceptance_run_id": row.acceptance_run_id, "acceptance_mode": row.acceptance_mode, "started_at": row.started_at, "completed_at": row.completed_at, "trade_date": row.trade_date, "market_session": row.market_session, "status": row.status, "integration_mode": row.integration_mode, "index_requested": row.index_requested, "index_returned": row.index_returned, "stock_requested": row.stock_requested, "stock_returned": row.stock_returned, "minute_stock_count": row.minute_stock_count, "external_call_count": row.external_call_count, "cache_hit_count": row.cache_hit_count, "database_insert_count": row.database_insert_count, "duplicate_count": row.duplicate_count, "index_coverage_ratio": row.index_coverage_ratio, "stock_coverage_ratio": row.stock_coverage_ratio, "minute_completeness_ratio": row.minute_completeness_ratio, "realtime_delay_p50": row.realtime_delay_p50, "realtime_delay_p95": row.realtime_delay_p95, "maximum_delay": row.maximum_delay, "provider_timestamp_ratio": row.provider_timestamp_ratio, "dual_source_match_count": row.dual_source_match_count, "material_conflict_count": row.material_conflict_count, "business_immutability_passed": row.business_immutability_passed, "report_path": row.report_path}
