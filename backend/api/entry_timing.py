from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict

from backend.core.responses import success_response
from backend.core.runtime_paths import output_root, tushare_cache_root
from database.session import get_session, init_db
from database.models.entry_timing_v22 import DeploymentV22Result, DeploymentV22Run
from entry_timing.service import EntryTimingShadowService
from entry_timing.config_v2 import EntryTimingV2ConfigService
from entry_timing.service_v2 import EntryTimingV2ShadowService
from entry_timing.historical_v22 import HistoricalV22Validator
from entry_timing.v22 import EntryTimingV22ConfigService
from sqlalchemy import func, select


router = APIRouter(prefix="/api/workbench/entry-timing", tags=["entry-timing-shadow"])


class EntryTimingRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    quant_run_id: str | None = None
    candidate_mode: Literal["QUANT_TOP100", "HISTORICAL_CANDIDATES"] = "QUANT_TOP100"
    confirm_shadow: bool = False


class EntryTimingV2RunRequest(EntryTimingRunRequest):
    candidate_mode: Literal["QUANT_TOP100", "HISTORICAL_CANDIDATES"] = "QUANT_TOP100"


class EntryTimingV22HistoricalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    historical_start: date
    historical_end: date
    confirm_shadow: bool = False


def _session():
    init_db()
    return get_session()


@router.post("/run-shadow")
def run_entry_timing_shadow(body: EntryTimingRunRequest, request: Request) -> dict:
    if not body.confirm_shadow:
        raise ValueError("ENTRY_TIMING_SHADOW_CONFIRMATION_REQUIRED")
    session = _session()
    try:
        data = EntryTimingShadowService(session).run(
            body.trade_date,
            quant_run_id=body.quant_run_id,
            candidate_mode=body.candidate_mode,
            force_shadow=True,
        )
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/latest")
def latest_entry_timing(request: Request, trade_date: date = Query(...)) -> dict:
    session = _session()
    try:
        data = EntryTimingShadowService(session).latest(trade_date)
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/results")
def entry_timing_results(
    request: Request,
    run_id: str = Query(..., min_length=1, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    pool_type: Literal["AI_POOL", "MANUAL_CHALLENGE_POOL"] | None = Query(default=None),
) -> dict:
    session = _session()
    try:
        data = EntryTimingShadowService(session).results(
            run_id, page=page, page_size=page_size, pool_type=pool_type
        )
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/methodology")
def entry_timing_methodology(request: Request) -> dict:
    data = {
        "version": "entry_timing_v1.0.0",
        "shadow_only": True,
        "production_enabled": False,
        "components": {
            "position": 25,
            "pullback": 20,
            "volume_price": 20,
            "sector": 15,
            "market": 10,
            "liquidity": 10,
        },
        "admission": {"PASS": ">=70 and all gates pass", "REVIEW": "60-69 or capped", "BLOCK": "<60 or a hard gate fails"},
        "selection_policy": "PASS only, maximum 20, never lower thresholds to fill quota",
    }
    return success_response(data=data, trace_id=request.state.trace_id)


@router.post("/v2/run-shadow")
def run_entry_timing_v2_shadow(body: EntryTimingV2RunRequest, request: Request) -> dict:
    if not body.confirm_shadow:
        raise ValueError("ENTRY_TIMING_V2_SHADOW_CONFIRMATION_REQUIRED")
    session = _session()
    try:
        data = EntryTimingV2ShadowService(session).run(
            body.trade_date,
            quant_run_id=body.quant_run_id,
            candidate_mode=body.candidate_mode,
            force_shadow=True,
        )
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/v2/latest")
def latest_entry_timing_v2(request: Request, trade_date: date = Query(...)) -> dict:
    session = _session()
    try:
        data = EntryTimingV2ShadowService(session).latest(trade_date)
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/v2/results")
def entry_timing_v2_results(
    request: Request,
    run_id: str = Query(..., min_length=1, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    strategy_id: str | None = Query(default=None, max_length=32),
    emotion_state: str | None = Query(default=None, max_length=32),
    market_regime: str | None = Query(default=None, max_length=32),
    admission_status_v1: str | None = Query(default=None, max_length=32),
    admission_status_v2: str | None = Query(default=None, max_length=32),
    pool_type: Literal["AI_POOL", "MANUAL_CHALLENGE_POOL"] | None = Query(default=None),
) -> dict:
    session = _session()
    try:
        data = EntryTimingV2ShadowService(session).results(
            run_id,
            page=page,
            page_size=page_size,
            strategy_id=strategy_id,
            emotion_state=emotion_state,
            market_regime=market_regime,
            admission_status_v1=admission_status_v1,
            admission_status=admission_status_v2,
            pool_type=pool_type,
        )
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/v2/methodology")
def entry_timing_v2_methodology(request: Request) -> dict:
    config = EntryTimingV2ConfigService().get()
    data = {
        "versions": {key: config[key] for key in ("classifier_version", "market_emotion_version", "entry_timing_version", "admission_version")},
        "shadow_only": True,
        "production_enabled": False,
        "weights": config["weights"],
        "strategy_thresholds": config["strategy_thresholds"],
        "selection_policy": "PASS only; never lower thresholds or fill an empty pool",
    }
    return success_response(data=data, trace_id=request.state.trace_id)


@router.post("/v22/historical-shadow")
def run_entry_timing_v22_historical(body: EntryTimingV22HistoricalRequest, request: Request) -> dict:
    if not body.confirm_shadow:
        raise ValueError("ENTRY_TIMING_V22_SHADOW_CONFIRMATION_REQUIRED")
    if body.historical_start > body.historical_end:
        raise ValueError("ENTRY_TIMING_V22_INVALID_DATE_RANGE")
    session = _session()
    try:
        output = (
            output_root()
            / body.historical_end.isoformat()
            / "复盘"
            / f"买入准入V2_2历史验证_{body.historical_start}_至_{body.historical_end}.xlsx"
        )
        data = HistoricalV22Validator(session, cache_root=tushare_cache_root()).run(
            body.historical_start, body.historical_end, output
        )
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


def _v22_row(row: DeploymentV22Result) -> dict:
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in {"id", "created_at", "updated_at"}
    }


@router.get("/v22/latest")
def latest_entry_timing_v22(request: Request, trade_date: date = Query(...)) -> dict:
    session = _session()
    try:
        run = session.scalar(
            select(DeploymentV22Run)
            .where(DeploymentV22Run.trade_date == trade_date)
            .order_by(DeploymentV22Run.created_at.desc())
        )
        if run is None:
            return success_response(data=None, trace_id=request.state.trace_id)
        data = {
            "run_id": run.run_id,
            "trade_date": run.trade_date,
            "regime_state": run.regime_state,
            "candidate_before": run.candidate_before,
            "after_regime": run.after_regime,
            "after_concentration": run.after_concentration,
            "triggered_count": run.triggered_count,
            "shadow_only": run.shadow_only,
            "llm_calls": run.llm_calls,
            "external_calls": run.external_calls,
            "orders_created": run.orders_created,
            "source_hashes": run.source_hashes_json,
        }
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/v22/results")
def entry_timing_v22_results(
    request: Request,
    run_id: str = Query(..., min_length=1, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    regime_state: str | None = Query(default=None, max_length=32),
    deployment_status: str | None = Query(default=None, max_length=32),
    crowding_status: str | None = Query(default=None, max_length=32),
    trigger_status: str | None = Query(default=None, max_length=32),
    pool_type: Literal["AI_POOL", "MANUAL_CHALLENGE_POOL"] | None = Query(default=None),
) -> dict:
    session = _session()
    try:
        filters = [DeploymentV22Result.run_id == run_id]
        for column, value in (
            (DeploymentV22Result.regime_state, regime_state),
            (DeploymentV22Result.deployment_status, deployment_status),
            (DeploymentV22Result.crowding_status, crowding_status),
            (DeploymentV22Result.trigger_status, trigger_status),
            (DeploymentV22Result.pool_type, pool_type),
        ):
            if value:
                filters.append(column == value)
        total = session.scalar(select(func.count()).select_from(DeploymentV22Result).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(DeploymentV22Result)
                .where(*filters)
                .order_by(DeploymentV22Result.admission_score.desc(), DeploymentV22Result.stock_code)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        data = {"items": [_v22_row(row) for row in rows], "total": total, "page": page, "page_size": page_size}
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/v22/methodology")
def entry_timing_v22_methodology(request: Request) -> dict:
    config = EntryTimingV22ConfigService().get()
    data = {
        "versions": config["versions"],
        "shadow_only": True,
        "production_enabled": False,
        "regime": config["regime"],
        "deployment": config["deployment"],
        "concentration": config["concentration"],
        "trigger": config["trigger"],
        "selection_policy": "Admission first, then regime, concentration, and intraday trigger; never lower thresholds.",
    }
    return success_response(data=data, trace_id=request.state.trace_id)
