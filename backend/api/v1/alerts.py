from __future__ import annotations

from fastapi import APIRouter, Query, Request

from alerts.service import IntradayAlertService
from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config


router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("/intraday/scan")
def run_intraday_scan(request: Request) -> dict:
    events = IntradayAlertService().run_intraday_scan()
    data = {
        "alerts": [event.model_dump(mode="json") for event in events],
        "count": len(events),
        "real_trading_enabled": get_app_config().real_trading_enabled,
    }
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/recent")
def recent_alerts(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
    events = IntradayAlertService().recent_alerts(limit=limit)
    data = {
        "alerts": [event.model_dump(mode="json") for event in events],
        "count": len(events),
        "real_trading_enabled": get_app_config().real_trading_enabled,
    }
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/config")
def alerts_config(request: Request) -> dict:
    data = IntradayAlertService().config_summary()
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)
