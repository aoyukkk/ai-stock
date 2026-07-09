from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config
from datasource.service import DataSourceService


router = APIRouter(prefix="/data-sources", tags=["data-sources"])


def _service() -> DataSourceService:
    return DataSourceService()


@router.get("/status")
def data_source_status(request: Request) -> dict:
    config = get_app_config()
    data = {
        "providers": _service().get_provider_statuses(),
        "real_trading_enabled": config.real_trading_enabled,
    }
    return success_response(
        data=sanitize_config(data),
        trace_id=request.state.trace_id,
    )


@router.get("/mock/stocks")
def mock_stocks(request: Request) -> dict:
    stocks = [
        stock.model_dump(mode="json")
        for stock in _service().get_stock_universe()
    ]
    return success_response(
        data={"stocks": stocks, "count": len(stocks)},
        trace_id=request.state.trace_id,
    )


@router.get("/mock/quotes")
def mock_quotes(
    request: Request,
    stock_codes: str | None = Query(default=None),
) -> dict:
    parsed_codes = [
        code.strip()
        for code in (stock_codes or "").split(",")
        if code.strip()
    ]
    quotes = [
        quote.model_dump(mode="json")
        for quote in _service().get_realtime_quotes(parsed_codes)
    ]
    return success_response(
        data={"quotes": quotes, "count": len(quotes)},
        trace_id=request.state.trace_id,
    )
