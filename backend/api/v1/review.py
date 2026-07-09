from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Body, Query, Request
from pydantic import BaseModel

from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config
from review.service import DailyReviewService


router = APIRouter(prefix="/review", tags=["review"])


class DailyReviewRunRequest(BaseModel):
    date: dt.date | None = None
    account_id: int | None = None
    use_mock_llm: bool | None = None


class DateOnlyRequest(BaseModel):
    date: dt.date | None = None


@router.post("/daily/run")
def run_daily_review(
    request: Request,
    payload: DailyReviewRunRequest | None = Body(default=None),
    query_date: dt.date | None = Query(default=None, alias="date"),
    query_account_id: int | None = Query(default=None, ge=1, alias="account_id"),
    query_use_mock_llm: bool | None = Query(default=None, alias="use_mock_llm"),
) -> dict:
    payload = payload or DailyReviewRunRequest()
    result = DailyReviewService().run_daily_review(
        review_date=payload.date or query_date,
        account_id=payload.account_id or query_account_id,
        use_mock_llm=payload.use_mock_llm if payload.use_mock_llm is not None else query_use_mock_llm,
    )
    data = result.model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/daily/{review_date}")
def get_daily_review(review_date: dt.date, request: Request) -> dict:
    result = DailyReviewService().get_daily_review(review_date)
    data = result.model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/predictions/evaluate")
def evaluate_predictions(
    request: Request,
    payload: DateOnlyRequest | None = Body(default=None),
    query_date: dt.date | None = Query(default=None, alias="date"),
) -> dict:
    payload = payload or DateOnlyRequest()
    results = DailyReviewService().evaluate_predictions(payload.date or query_date)
    data = {
        "results": [result.model_dump(mode="json") for result in results],
        "count": len(results),
        "real_trading_enabled": get_app_config().real_trading_enabled,
    }
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/order-plans/evaluate")
def evaluate_order_plans(
    request: Request,
    payload: DateOnlyRequest | None = Body(default=None),
    query_date: dt.date | None = Query(default=None, alias="date"),
) -> dict:
    payload = payload or DateOnlyRequest()
    results = DailyReviewService().evaluate_order_plans(payload.date or query_date)
    data = {
        "results": [result.model_dump(mode="json") for result in results],
        "count": len(results),
        "real_trading_enabled": get_app_config().real_trading_enabled,
    }
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/config")
def review_config(request: Request) -> dict:
    data = DailyReviewService().config_summary()
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)
