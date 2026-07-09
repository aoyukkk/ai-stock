from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config
from agents.committee import AICommitteeService


router = APIRouter(prefix="/committee", tags=["committee"])


@router.get("/run")
def run_committee(
    request: Request,
    input_top_n: int | None = Query(default=None, ge=1),
    final_top_n: int | None = Query(default=None, ge=1),
    persist: bool = Query(default=False),
) -> dict:
    ranking = AICommitteeService().run_committee(
        input_top_n=input_top_n,
        final_top_n=final_top_n,
        persist=persist,
    )
    data = ranking.model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/config")
def committee_config(request: Request) -> dict:
    data = AICommitteeService().config_summary()
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)
