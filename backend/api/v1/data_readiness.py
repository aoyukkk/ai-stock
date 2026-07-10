from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from backend.core.responses import success_response
from temporal.readiness import DataReadinessService
from temporal.schemas import RunMode


router = APIRouter(prefix="/data-readiness", tags=["data-readiness"])


class ReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_mode: RunMode = RunMode.POST_MARKET_FINAL
    decision_time: datetime | None = None
    base_market_trade_date: str | None = None
    target_trade_date: str | None = None
    allow_provisional: bool = False


@router.get("/current")
def current_readiness(request: Request) -> dict:
    service = DataReadinessService()
    context, manifest = service.check(RunMode.POST_MARKET_FINAL)
    return success_response(data=service.payload(context, manifest), trace_id=request.state.trace_id)


@router.post("/check")
def check_readiness(body: ReadinessRequest, request: Request) -> dict:
    from datetime import date
    service = DataReadinessService()
    context, manifest = service.check(
        body.run_mode, body.decision_time,
        base_market_trade_date=date.fromisoformat(body.base_market_trade_date) if body.base_market_trade_date else None,
        target_trade_date=date.fromisoformat(body.target_trade_date) if body.target_trade_date else None,
        allow_provisional=body.allow_provisional,
    )
    return success_response(data=service.payload(context, manifest), trace_id=request.state.trace_id)
