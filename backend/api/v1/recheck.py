from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config
from recheck.service import RecheckService


router = APIRouter(prefix="/recheck", tags=["recheck"])


@router.post("/pre-market/run")
def run_pre_market_recheck(request: Request, limit: int | None = Query(default=None, ge=1)) -> dict:
    results = RecheckService().run_pre_market(limit=limit)
    data = {
        "results": [result.model_dump(mode="json") for result in results],
        "count": len(results),
        "real_trading_enabled": get_app_config().real_trading_enabled,
    }
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/order-plans/{order_plan_id}")
def recheck_order_plan(order_plan_id: int, request: Request) -> dict:
    result = RecheckService().recheck_order_plan(order_plan_id)
    data = result.model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/config")
def recheck_config(request: Request) -> dict:
    data = RecheckService().config_summary()
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)
