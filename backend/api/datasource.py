from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from backend.core.responses import error_response, success_response
from datasource.akshare_provider import AKShareMarketDataProvider
from datasource.baostock_provider import BaoStockMarketDataProvider
from datasource.mock.market_provider import MockMarketDataProvider
from datasource.ths.adapter import THSMarketDataProvider


router = APIRouter(prefix="/api/datasource", tags=["datasource-debug"])


@router.get("/providers")
def providers(request: Request) -> dict:
    return success_response(
        data={
            "providers": [
                {"name": "mock", "type": "mock", "enabled": True},
                {"name": "akshare", "type": "debug", "enabled": True},
                {"name": "baostock", "type": "debug", "enabled": True},
                {"name": "ths_stub", "type": "stub", "enabled": True},
            ]
        },
        trace_id=request.state.trace_id,
    )


@router.get("/stock-list")
def stock_list(request: Request, provider: str = Query(default="mock")) -> dict:
    return _handle(request, provider, lambda selected: {"stocks": selected.get_stock_list()})


@router.get("/realtime")
def realtime(
    request: Request,
    provider: str = Query(default="mock"),
    stock_code: str = Query(default="000001"),
) -> dict:
    return _handle(request, provider, lambda selected: selected.get_realtime(stock_code))


@router.get("/kline")
def kline(
    request: Request,
    provider: str = Query(default="mock"),
    stock_code: str = Query(default="000001"),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    frequency: str = Query(default="daily"),
) -> dict:
    return _handle(
        request,
        provider,
        lambda selected: {
            "bars": selected.get_kline(
                stock_code,
                start_date=start_date,
                end_date=end_date,
                frequency=frequency,
            )
        },
    )


@router.get("/finance")
def finance(
    request: Request,
    provider: str = Query(default="mock"),
    stock_code: str = Query(default="000001"),
) -> dict:
    return _handle(request, provider, lambda selected: selected.get_finance(stock_code))


@router.get("/capital-flow")
def capital_flow(
    request: Request,
    provider: str = Query(default="mock"),
    stock_code: str = Query(default="000001"),
) -> dict:
    return _handle(request, provider, lambda selected: selected.get_capital_flow(stock_code))


@router.get("/market-emotion")
def market_emotion(request: Request, provider: str = Query(default="mock")) -> dict:
    return _handle(request, provider, lambda selected: selected.get_market_emotion())


@router.get("/limit-price")
def limit_price(
    request: Request,
    provider: str = Query(default="mock"),
    stock_code: str = Query(default="000001"),
) -> dict:
    return _handle(request, provider, lambda selected: selected.get_limit_price(stock_code))


@router.get("/pre-market-auction")
def pre_market_auction(
    request: Request,
    provider: str = Query(default="mock"),
    stock_code: str = Query(default="000001"),
) -> dict:
    return _handle(request, provider, lambda selected: selected.get_pre_market_auction(stock_code))


def _handle(request: Request, provider: str, operation: Callable[[Any], Any]) -> dict:
    try:
        selected = _provider(provider)
        return success_response(
            data=_serialize(operation(selected)),
            message="manual datasource request completed",
            trace_id=request.state.trace_id,
        )
    except NotImplementedError as exc:
        return error_response(
            code="PROVIDER_NOT_IMPLEMENTED",
            message=str(exc),
            data={"provider": provider},
            trace_id=request.state.trace_id,
        )
    except Exception as exc:
        return error_response(
            code="DATASOURCE_ERROR",
            message=str(exc),
            data={"provider": provider},
            trace_id=request.state.trace_id,
        )


def _provider(name: str) -> Any:
    normalized = name.lower()
    if normalized == "mock":
        return MockMarketDataProvider()
    if normalized == "akshare":
        return AKShareMarketDataProvider()
    if normalized == "baostock":
        return BaoStockMarketDataProvider()
    if normalized == "ths_stub":
        return THSMarketDataProvider()
    raise ValueError(f"Unknown datasource provider: {name}")


def _serialize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value
