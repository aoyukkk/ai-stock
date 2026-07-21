from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.core.config import get_app_config
from backend.core.responses import success_response
from database.session import get_session, init_db
from midday.excel import MiddayRecommendationExcelService
from midday.service import MiddayRecommendationService
from database.models.intraday_monitor import IntradayMonitorSession
from database.models.midday import MiddayRecommendationResult, MiddayRecommendationRun
from intraday_monitor.broker import RequestPriority, market_data_broker
from intraday_monitor.coordinator import MiddayCompatibilityGuard
from midday.provider import MiddayIFindCollector
from sqlalchemy import select
from backend.core.runtime_paths import output_root
from database.models import MiddayV22AfternoonRun, MiddayV22Result, MiddayV22Run
from midday.v22_afternoon_service import MiddayV22AfternoonRecheckService
from midday.v22_service import MiddayV22OneShotService


router = APIRouter(tags=["midday-recommendation"])


class MiddayRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    historical_validation: bool = False
    force: bool = False


class MiddayRunIdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=64)


class MiddayV22RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    cutoff_time: time = time(11, 30)


class MiddayV22RecheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    stocks: list[str] = Field(default_factory=list, max_length=20)


def _session():
    init_db()
    return get_session()


def _run(body: MiddayRunRequest) -> None:
    session = _session()
    guard = MiddayCompatibilityGuard()
    try:
        monitor = session.scalar(select(IntradayMonitorSession).where(
            IntradayMonitorSession.trade_date == body.trade_date,
        ).order_by(IntradayMonitorSession.created_at.desc()))
        guard.begin_midday(monitor)
        session.commit()
        MiddayRecommendationService(session).run(
            body.trade_date,
            historical_validation=body.historical_validation,
            force=body.force,
        )
    finally:
        guard.end_midday()
        session.close()


def _run_v22(body: MiddayV22RunRequest) -> None:
    session = _session()
    try: MiddayV22OneShotService(session, output_root=output_root()).run(body.trade_date, body.cutoff_time)
    finally: session.close()


def _recheck_v22(body: MiddayV22RecheckRequest) -> None:
    session = _session()
    try:
        stocks = body.stocks
        if not stocks:
            midday_run = session.scalar(select(MiddayV22Run).where(MiddayV22Run.trade_date == body.trade_date).order_by(MiddayV22Run.created_at.desc()))
            if midday_run:
                rows = session.scalars(select(MiddayV22Result).where(MiddayV22Result.run_id == midday_run.run_id)).all()
                stocks = [row.stock_code for row in rows if row.result_layer in {"BUY_READY", "AFTERNOON_WATCH", "REGIME_BLOCKED_HIGH_SCORE", "CONCENTRATION_REVIEW"}]
        MiddayV22AfternoonRecheckService(session, output_root=output_root()).run(body.trade_date, stocks)
    finally: session.close()


@router.post("/api/workbench/midday/run")
def run_midday(body: MiddayRunRequest, background_tasks: BackgroundTasks, request: Request) -> dict:
    background_tasks.add_task(_run, body)
    return success_response(data={"status": "QUEUED", "trade_date": body.trade_date, "historical_validation": body.historical_validation}, trace_id=request.state.trace_id)


@router.post("/api/workbench/midday/v22/run")
def run_midday_v22(body: MiddayV22RunRequest, background_tasks: BackgroundTasks, request: Request) -> dict:
    background_tasks.add_task(_run_v22, body)
    return success_response(data={"status": "QUEUED", "trade_date": body.trade_date, "cutoff_time": body.cutoff_time}, trace_id=request.state.trace_id)


@router.get("/api/workbench/midday/v22/status")
def midday_v22_status(request: Request, trade_date: date) -> dict:
    session = _session()
    try:
        run = session.scalar(select(MiddayV22Run).where(MiddayV22Run.trade_date == trade_date).order_by(MiddayV22Run.created_at.desc()))
        recheck = session.scalar(select(MiddayV22AfternoonRun).where(MiddayV22AfternoonRun.trade_date == trade_date).order_by(MiddayV22AfternoonRun.created_at.desc()))
        if run is None: return success_response(data={"status": "NOT_RUN"}, trace_id=request.state.trace_id)
        data = {"run_id": run.run_id, "trade_date": run.trade_date, "decision_time": run.cutoff_time, "status": run.status, "current_stage": run.current_stage, "previous_regime": run.previous_regime, "midday_regime": run.midday_regime, "counts": run.counts_json, "excel_path": (run.output_paths_json or {}).get("excel"), "afternoon_recheck_run_id": recheck.run_id if recheck else None, "afternoon_recheck_status": recheck.status if recheck else "NOT_RUN"}
        return success_response(data=data, trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/api/workbench/midday/v22/results")
def midday_v22_results(request: Request, run_id: str, page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=100)) -> dict:
    session = _session()
    try:
        rows = list(session.scalars(select(MiddayV22Result).where(MiddayV22Result.run_id == run_id).order_by(MiddayV22Result.id)))
        start = (page - 1) * page_size
        return success_response(data={"items": [row.payload_json for row in rows[start:start+page_size]], "total": len(rows), "page": page, "page_size": page_size}, trace_id=request.state.trace_id)
    finally: session.close()


@router.post("/api/workbench/midday/v22/recheck")
def midday_v22_recheck(body: MiddayV22RecheckRequest, background_tasks: BackgroundTasks, request: Request) -> dict:
    background_tasks.add_task(_recheck_v22, body)
    return success_response(data={"status": "QUEUED", "trade_date": body.trade_date, "stocks": body.stocks}, trace_id=request.state.trace_id)


@router.get("/api/workbench/midday/status")
def midday_status(request: Request, run_id: str | None = Query(default=None), trade_date: date | None = Query(default=None)) -> dict:
    session = _session()
    try:
        return success_response(data=MiddayRecommendationService(session).status(run_id, trade_date=trade_date), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/midday/results")
def midday_results(request: Request, run_id: str = Query(...), page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=100)) -> dict:
    session = _session()
    try:
        return success_response(data=MiddayRecommendationService(session).results(run_id, page=page, page_size=page_size), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/midday/history")
def midday_history(request: Request, trade_date: date | None = Query(default=None)) -> dict:
    session = _session()
    try:
        items = MiddayRecommendationService(session).history(trade_date)
        return success_response(data={"items": items, "count": len(items)}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/api/workbench/midday/recheck")
def midday_recheck(body: MiddayRunIdRequest, request: Request) -> dict:
    session = _session()
    try:
        run = session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.run_id == body.run_id))
        if run is None:
            raise ValueError("MIDDAY_RUN_NOT_FOUND")
        rows = list(session.scalars(select(MiddayRecommendationResult).where(
            MiddayRecommendationResult.run_id == body.run_id,
            MiddayRecommendationResult.pro_rank.is_not(None),
        ).order_by(MiddayRecommendationResult.pro_rank)))
        codes = [row.stock_code for row in rows]
        app_config = get_app_config()
        config = app_config.config_files.get("midday_recommendation", {}).get("midday_recommendation", {}).get("ifind", {})
        collector = MiddayIFindCollector(session, app_config, config)
        with market_data_broker.request(RequestPriority.P0, "MIDDAY_AFTERNOON_RECHECK", timeout_seconds=30):
            collected = collector.collect_snapshots(run.session_trade_date, codes)
        snapshots = {row.stock_code: row for row in collected["stock_rows"]}
        result = MiddayRecommendationService(session, app_config).recheck(body.run_id, snapshots=snapshots)
        result.update({
            "snapshot_requested": len(codes),
            "snapshot_returned": len(snapshots),
            "index_returned": len(collected["index_rows"]),
            "external_calls": collector.summary()["total"],
        })
        return success_response(data=result, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/api/workbench/midday/export")
def midday_export(body: MiddayRunIdRequest, request: Request) -> dict:
    session = _session()
    try:
        output_root = Path(get_app_config().root_dir) / "outputs"
        return success_response(data=MiddayRecommendationExcelService(session, output_root).export(body.run_id), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/midday/methodology")
def midday_methodology(request: Request) -> dict:
    session = _session()
    try:
        return success_response(data=MiddayRecommendationService(session).methodology(), trace_id=request.state.trace_id)
    finally:
        session.close()
