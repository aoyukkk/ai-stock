from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.core.config import get_app_config
from backend.core.responses import error_response, success_response
from database.session import get_session, init_db
from post_close.excel import PostCloseActionExcelService
from post_close.positions import PositionImportService
from post_close.pro_review import PostCloseProReviewService
from post_close.service import IFindEnhancementService, PostCloseActionService


router = APIRouter(tags=["post-close-actions"])


class EnhancementRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    quant_run_id: str | None = Field(default=None, max_length=64)
    provider_run_id: str | None = Field(default=None, max_length=64)


class FastRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    run_mode: Literal["POST_CLOSE_FAST", "POST_CLOSE_FINAL"] = "POST_CLOSE_FAST"
    include_ai_simulation: bool = False
    force: bool = False


class ProReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_run_id: str = Field(min_length=1, max_length=64)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_run_id: str = Field(min_length=1, max_length=64)


class PositionImportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=8_000_000)
    account_scope: Literal["HUMAN_REFERENCE", "AI_SIMULATION"] = "HUMAN_REFERENCE"


class PositionImportConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=64)


class PositionEmptyConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    account_scopes: list[Literal["HUMAN_REFERENCE", "AI_SIMULATION"]] = Field(default_factory=lambda: ["HUMAN_REFERENCE"], min_length=1, max_length=2)
    confirmed_by: str = Field(default="LOCAL_TRADER", min_length=1, max_length=64)


def _session():
    init_db()
    return get_session()


def _run_pro(action_run_id: str) -> None:
    session = _session()
    try:
        PostCloseProReviewService(session).run(action_run_id)
    finally:
        session.close()


@router.post("/api/workbench/ifind-enhancement/run-shadow")
def run_shadow_enhancement(body: EnhancementRunRequest, request: Request) -> dict:
    session = _session()
    try:
        result = IFindEnhancementService(session).run_shadow(body.trade_date, quant_run_id=body.quant_run_id, provider_run_id=body.provider_run_id)
        return success_response(data=result, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/ifind-enhancement/latest")
def latest_shadow_enhancement(request: Request, trade_date: date = Query(...)) -> dict:
    session = _session()
    try:
        from database.models import IFindEnhancementRun
        from sqlalchemy import select
        row = session.scalar(select(IFindEnhancementRun).where(IFindEnhancementRun.trade_date == trade_date).order_by(IFindEnhancementRun.created_at.desc()))
        data = IFindEnhancementService(session).summary(row.run_id) if row else None
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/api/workbench/post-close-actions/run-fast")
def run_fast(body: FastRunRequest, request: Request) -> dict:
    session = _session()
    try:
        result = PostCloseActionService(session).run_fast(
            body.trade_date,
            run_mode=body.run_mode,
            include_ai_simulation=body.include_ai_simulation,
            force=body.force,
        )
        return success_response(data=result, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/api/workbench/post-close-actions/run-pro-review")
def run_pro_review(body: ProReviewRequest, background_tasks: BackgroundTasks, request: Request) -> dict:
    background_tasks.add_task(_run_pro, body.action_run_id)
    return success_response(data={"action_run_id": body.action_run_id, "status": "QUEUED", "fast_result_preserved": True}, trace_id=request.state.trace_id)


@router.get("/api/workbench/post-close-actions/status")
def action_status(
    request: Request,
    action_run_id: str | None = Query(default=None),
    trade_date: date | None = Query(default=None),
) -> dict:
    session = _session()
    try:
        return success_response(
            data=PostCloseActionService(session).status(action_run_id, trade_date=trade_date),
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


@router.get("/api/workbench/post-close-actions/results")
def action_results(
    request: Request, action_run_id: str | None = Query(default=None), trade_date: date | None = Query(default=None),
    held_only: bool | None = Query(default=None),
    action: str | None = Query(default=None), source: str | None = Query(default=None),
    requires_manual_review: bool | None = Query(default=None), page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100), sort_by: str = Query(default="stock_code"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
) -> dict:
    session = _session()
    try:
        service = PostCloseActionService(session)
        resolved_run_id = action_run_id
        if resolved_run_id is None:
            latest = service.status(trade_date=trade_date)
            resolved_run_id = latest.get("run_id")
        if resolved_run_id is None:
            result = {"items": [], "total": 0, "page": page, "page_size": page_size}
        else:
            result = service.results(resolved_run_id, held_only=held_only, action=action, source=source, requires_manual_review=requires_manual_review, page=page, page_size=page_size, sort_by=sort_by, sort_order=sort_order)
        return success_response(data=result, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/post-close-actions/history")
def action_history(request: Request, trade_date: date | None = Query(default=None)) -> dict:
    session = _session()
    try:
        items = PostCloseActionService(session).history(trade_date)
        return success_response(data={"items": items, "count": len(items)}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/post-close-actions/compare")
def action_compare(request: Request, action_run_id: str | None = Query(default=None), trade_date: date | None = Query(default=None)) -> dict:
    session = _session()
    try:
        service = PostCloseActionService(session)
        if trade_date is not None:
            data = service.compare_fast_final(trade_date)
        elif action_run_id is not None:
            data = service.compare(action_run_id)
        else:
            data = {"status": "NOT_COMPARABLE", "reason": "RUN_OR_TRADE_DATE_REQUIRED"}
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/api/workbench/post-close-actions/export")
def action_export(body: ExportRequest, request: Request) -> dict:
    session = _session()
    try:
        output_root = Path(get_app_config().root_dir) / "outputs"
        return success_response(data=PostCloseActionExcelService(session, output_root).export(body.action_run_id), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/positions/snapshot")
@router.get("/api/workbench/positions/current")
def current_positions(
    request: Request,
    account_scope: str | None = Query(default=None),
    trade_date: date | None = Query(default=None),
    include_ai_simulation: bool = Query(default=False),
) -> dict:
    session = _session()
    try:
        rows = PositionImportService(session).current(account_scope)
        items = [{"account_scope": row.account_scope, "snapshot_time": row.snapshot_time, "trade_date": row.trade_date, "stock_code": row.stock_code, "stock_name": row.stock_name_snapshot, "quantity": row.quantity, "available_quantity": row.available_quantity, "cost_price": float(row.cost_price), "buy_date": row.buy_date, "market_value": float(row.market_value) if row.market_value is not None else None, "position_percent": float(row.position_percent) if row.position_percent is not None else None, "source": row.source, "version": row.version} for row in rows]
        target_date = trade_date or date.today()
        truth = PostCloseActionService(session).position_truth(
            target_date,
            include_ai_simulation=include_ai_simulation,
        )
        return success_response(
            data={"items": items, "count": len(items), **truth},
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


@router.get("/api/workbench/positions/truth-status")
def position_truth_status(
    request: Request,
    trade_date: date = Query(...),
    include_ai_simulation: bool = Query(default=False),
) -> dict:
    session = _session()
    try:
        return success_response(
            data=PostCloseActionService(session).position_truth(
                trade_date,
                include_ai_simulation=include_ai_simulation,
            ),
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


@router.post("/api/workbench/positions/confirm-empty")
def position_confirm_empty(body: PositionEmptyConfirmRequest, request: Request) -> dict:
    session = _session()
    try:
        service = PositionImportService(session)
        scopes = list(dict.fromkeys(body.account_scopes))
        items = [service.confirm_empty(account_scope=scope, trade_date_value=body.trade_date, confirmed_by=body.confirmed_by) for scope in scopes]
        return success_response(
            data={
                "status": "CONFIRMED_EMPTY",
                "items": items,
                "truth": PostCloseActionService(session).position_truth(
                    body.trade_date,
                    include_ai_simulation="AI_SIMULATION" in scopes,
                ),
            },
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


@router.post("/api/workbench/positions/import-preview")
def position_import_preview(body: PositionImportPreviewRequest, request: Request) -> dict:
    session = _session()
    try:
        return success_response(data=PositionImportService(session).preview(filename=body.filename, content_base64=body.content_base64, account_scope=body.account_scope), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/api/workbench/positions/import-confirm")
def position_import_confirm(body: PositionImportConfirmRequest, request: Request) -> dict:
    session = _session()
    try:
        return success_response(data=PositionImportService(session).confirm(body.preview_id), trace_id=request.state.trace_id)
    finally:
        session.close()
