from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config
from trading.service import VirtualTradingService


router = APIRouter(prefix="/virtual-trading", tags=["virtual-trading"])


class RepriceRequest(BaseModel):
    new_price: Decimal
    reason: str | None = None


@router.post("/accounts/default")
def create_default_account(request: Request) -> dict:
    data = VirtualTradingService().create_default_ai_account().model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/account")
def account_summary(
    request: Request,
    account_id: int | None = Query(default=None, ge=1),
) -> dict:
    data = VirtualTradingService().get_account_summary(account_id=account_id).model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/run-plans")
def run_plans(
    request: Request,
    input_top_n: int | None = Query(default=None, ge=1),
    account_id: int | None = Query(default=None, ge=1),
    persist_plans: bool = Query(default=False),
) -> dict:
    report = VirtualTradingService().run_order_plans(
        input_top_n=input_top_n,
        account_id=account_id,
        persist_plans=persist_plans,
    )
    data = report.model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/orders")
def get_orders(
    request: Request,
    account_id: int | None = Query(default=None, ge=1),
) -> dict:
    orders = VirtualTradingService().get_orders(account_id=account_id)
    data = {
        "orders": [order.model_dump(mode="json") for order in orders],
        "real_trading_enabled": get_app_config().real_trading_enabled,
    }
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/positions")
def get_positions(
    request: Request,
    account_id: int | None = Query(default=None, ge=1),
) -> dict:
    positions = VirtualTradingService().get_positions(account_id=account_id)
    data = {
        "positions": [position.model_dump(mode="json") for position in positions],
        "real_trading_enabled": get_app_config().real_trading_enabled,
    }
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/trades")
def get_trades(
    request: Request,
    account_id: int | None = Query(default=None, ge=1),
) -> dict:
    trades = VirtualTradingService().get_trades(account_id=account_id)
    data = {
        "trades": [trade.model_dump(mode="json") for trade in trades],
        "real_trading_enabled": get_app_config().real_trading_enabled,
    }
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, request: Request, reason: str | None = Query(default=None)) -> dict:
    data = VirtualTradingService().cancel_order(order_id, reason=reason).model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/orders/{order_id}/reprice")
def reprice_order(order_id: int, payload: RepriceRequest, request: Request) -> dict:
    data = VirtualTradingService().reprice_order(
        order_id,
        new_price=payload.new_price,
        reason=payload.reason,
    ).model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)
