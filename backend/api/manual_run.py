from __future__ import annotations

from fastapi import APIRouter, Request

from backend.core.responses import success_response


router = APIRouter(prefix="/api/manual", tags=["manual-run"])


@router.get("/status")
def manual_status(request: Request) -> dict:
    return success_response(
        data={
            "execution_mode": "manual",
            "scheduler_enabled": False,
            "auto_market_tasks_enabled": False,
            "available_steps": [
                "stock_list",
                "realtime",
                "kline",
                "finance",
                "capital_flow",
                "market_emotion",
                "limit_price",
                "pre_market_auction",
            ],
            "available_providers": ["mock", "akshare", "baostock", "ths_stub"],
        },
        message="manual debug mode",
        trace_id=request.state.trace_id,
    )
