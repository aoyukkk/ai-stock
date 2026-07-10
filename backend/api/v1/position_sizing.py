from __future__ import annotations

import hashlib
from uuid import uuid4
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.core.responses import error_response, success_response
from database.models.allocation import AllocationRun, PositionSuggestionRecord
from database.models.quant_run import QuantRun
from database.models.temporal import RunDataManifestRecord
from database.models.order_plan import OrderPlan
from database.session import get_session, init_db
from position_sizing.config import load_position_sizing_config
from position_sizing.engine import PositionSizingEngine
from position_sizing.repository import AllocationRepository
from position_sizing.schemas import AccountState, SizingCandidate


router = APIRouter(prefix="/position-sizing", tags=["position-sizing"])


class PositionSizingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recommendation_run_id: str | None = Field(default=None, max_length=128)
    account_id: str | None = Field(default=None, max_length=128)
    dry_run: bool = True
    account: AccountState
    candidates: list[SizingCandidate] = Field(min_length=1, max_length=50)
    quant_run_id: str | None = None
    run_data_manifest_id: str | None = None
    order_plan_id: int | None = None
    account_snapshot_time: datetime | None = None


@router.post("/evaluate")
def evaluate_position_sizing(body: PositionSizingRequest, request: Request) -> dict:
    config = load_position_sizing_config()
    result = PositionSizingEngine(config).evaluate(body.account, body.candidates)
    request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    init_db()
    session = get_session()
    try:
        temporal_error = _validate_temporal_inputs(session, body)
        if temporal_error:
            return error_response(
                "TEMPORAL_POSITION_SIZING_BLOCKED",
                "Position sizing inputs do not share an actionable temporal manifest.",
                data={"reason": temporal_error}, trace_id=request.state.trace_id,
            )
        existing = session.scalar(select(AllocationRun).where(AllocationRun.request_hash == request_hash))
        if existing:
            return success_response(data=_allocation_payload(session, existing), trace_id=request.state.trace_id)
        run_id = f"allocation-{uuid4().hex}"
        row = AllocationRepository(session).save({
            "run_id": run_id,
            "recommendation_run_id": body.recommendation_run_id,
            "account_id": body.account_id,
            "engine_version": result.version,
            "advisory_only": True,
            "dry_run": body.dry_run,
            "config_snapshot": config.model_dump(mode="json"),
            "total_suggested_capital": result.total_suggested_capital,
            "total_maximum_planned_loss": result.total_maximum_planned_loss,
            "request_hash": request_hash,
            "quant_run_id": body.quant_run_id,
            "run_data_manifest_id": body.run_data_manifest_id,
            "order_plan_id": body.order_plan_id,
            "account_snapshot_time": body.account_snapshot_time.isoformat() if body.account_snapshot_time else None,
        }, result)
        return success_response(data=_allocation_payload(session, row), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/runs/latest")
def get_latest_position_sizing_run(request: Request) -> dict:
    session = get_session()
    try:
        row = AllocationRepository(session).latest()
        if row is None:
            return error_response("ALLOCATION_RUN_NOT_FOUND", "No allocation run exists.", trace_id=request.state.trace_id)
        return success_response(data=_allocation_payload(session, row), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/runs/{run_id}")
def get_position_sizing_run(run_id: str, request: Request) -> dict:
    session = get_session()
    try:
        row = session.scalar(select(AllocationRun).where(AllocationRun.run_id == run_id))
        if row is None:
            return error_response("ALLOCATION_RUN_NOT_FOUND", "Allocation run was not found.", trace_id=request.state.trace_id)
        return success_response(data=_allocation_payload(session, row), trace_id=request.state.trace_id)
    finally:
        session.close()


def _allocation_payload(session, row: AllocationRun) -> dict:
    suggestions = session.scalars(select(PositionSuggestionRecord).where(PositionSuggestionRecord.run_id == row.run_id)).all()
    return {
        "run_id": row.run_id, "version": row.engine_version, "advisory_only": row.advisory_only,
        "dry_run": row.dry_run, "total_suggested_capital": str(row.total_suggested_capital),
        "total_maximum_planned_loss": str(row.total_maximum_planned_loss), "execution_capability": "NONE_ADVISORY_ONLY",
        "suggestions": [{"stock_code": item.stock_code, "status": item.status, "relative_allocation_weight": str(item.relative_allocation_weight), "account_position_percent": str(item.account_position_percent), "suggested_capital": str(item.suggested_capital), "suggested_quantity": item.suggested_quantity, "risk_per_share": str(item.risk_per_share), "maximum_planned_loss": str(item.maximum_planned_loss), "binding_constraint": item.binding_constraint, "constraint_quantities": item.constraint_quantities, "warnings": item.warnings} for item in suggestions],
    }


def _validate_temporal_inputs(session, body: PositionSizingRequest) -> str | None:
    references = (body.quant_run_id, body.run_data_manifest_id, body.order_plan_id, body.account_snapshot_time)
    if not any(value is not None for value in references):
        return None  # explicit debug candidate mode remains advisory-only
    if body.quant_run_id is None or body.run_data_manifest_id is None or body.account_snapshot_time is None:
        return "TEMPORAL_REFERENCES_INCOMPLETE"
    run = session.scalar(select(QuantRun).where(QuantRun.run_id == body.quant_run_id))
    manifest = session.scalar(select(RunDataManifestRecord).where(RunDataManifestRecord.manifest_id == body.run_data_manifest_id))
    if run is None or manifest is None or run.data_manifest_id != manifest.manifest_id:
        return "RUN_DATA_MANIFEST_MISMATCH"
    if not run.actionable or not manifest.actionable:
        return "NON_ACTIONABLE"
    snapshot = body.account_snapshot_time
    decision = run.decision_time
    if snapshot.tzinfo is None:
        return "ACCOUNT_SNAPSHOT_TIMEZONE_REQUIRED"
    if decision.tzinfo is None:
        decision = decision.replace(tzinfo=snapshot.tzinfo)
    if snapshot > decision or decision - snapshot > timedelta(minutes=30):
        return "ACCOUNT_SNAPSHOT_STALE"
    if body.order_plan_id is not None:
        plan = session.get(OrderPlan, body.order_plan_id)
        if plan is None or plan.plan_date != run.target_trade_date:
            return "ORDER_PLAN_TARGET_DATE_MISMATCH"
    return None
