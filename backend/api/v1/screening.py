from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config
from screening.service import LightScreeningService


router = APIRouter(prefix="/screening/light", tags=["screening"])


@router.get("/run")
def run_light_screening(
    request: Request,
    quant_top_q: int | None = Query(default=None, ge=1),
    top_n: int | None = Query(default=None, ge=1),
    persist: bool = Query(default=False),
) -> dict:
    ranking = LightScreeningService().run_light_screening(
        quant_top_q=quant_top_q,
        top_n=top_n,
        persist=persist,
    )
    data = ranking.model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(
        data=sanitize_config(data),
        trace_id=request.state.trace_id,
    )


@router.get("/config")
def light_screening_config(request: Request) -> dict:
    data = LightScreeningService().config_summary()
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(
        data=sanitize_config(data),
        trace_id=request.state.trace_id,
    )
