from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config
from quant.config import load_quant_config
from quant.service import QuantService


router = APIRouter(prefix="/quant", tags=["quant"])


@router.get("/scan")
def run_quant_scan(
    request: Request,
    top_q: int | None = Query(default=None, ge=1),
    persist: bool = Query(default=False),
) -> dict:
    ranking = QuantService().run_quant_scan(top_q=top_q, persist=persist)
    data = ranking.model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(
        data=sanitize_config(data),
        trace_id=request.state.trace_id,
    )


@router.get("/config")
def quant_config(request: Request) -> dict:
    config = load_quant_config()
    return success_response(
        data=sanitize_config(config.summary()),
        trace_id=request.state.trace_id,
    )
