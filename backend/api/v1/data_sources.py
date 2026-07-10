from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config
from datasource.service import DataSourceService
from fundamentals.tushare_service import TushareFundamentalBatchService


router = APIRouter(prefix="/data-sources", tags=["data-sources"])


class FundamentalPrewarmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    periods: list[str] = Field(min_length=1, max_length=20)
    interfaces: list[str] = Field(default_factory=list, max_length=20)
    mainbz_types: list[str] = Field(default_factory=lambda: ["P", "I", "D"])
    force: bool = False
    dry_run: bool = True


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


@router.post("/tushare/fundamental/prewarm")
def prewarm_tushare_fundamental(body: FundamentalPrewarmRequest, request: Request) -> dict:
    data = TushareFundamentalBatchService().prewarm(
        body.periods,
        body.interfaces or None,
        mainbz_types=body.mainbz_types,
        force=body.force,
        dry_run=body.dry_run,
    )
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/tushare/fundamental/status")
def tushare_fundamental_status(request: Request) -> dict:
    provider = TushareFundamentalBatchService().provider
    return success_response(
        data={
            "configured": "CONFIGURED" if provider.token_configured() else "NOT_CONFIGURED",
            "cache_enabled": provider.cache_enabled,
            "batch_dimension": "report_period",
            "per_stock_api_call_count": provider.per_stock_api_call_count,
        },
        trace_id=request.state.trace_id,
    )
