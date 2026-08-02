from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from backend.core.responses import success_response
from database.models import FactorPerformanceHistory, ForwardOutcome, GateValueEvaluation, ModelVersionComparison
from database.session import get_session, init_db
from review.forward_shadow import HORIZONS, sample_status


router = APIRouter(prefix="/api/forward-shadow", tags=["forward-shadow-read-only"])


def _serialize(row, fields):
    return {field: getattr(row, field) for field in fields}


@router.get("/summary")
def summary(request: Request, as_of_date: date = Query(...)) -> dict:
    init_db(); session = get_session()
    try:
        rows = list(session.scalars(select(ForwardOutcome).where(ForwardOutcome.entry_trade_date <= as_of_date)))
        matured = {f"d{h}": sum((r.horizon_status_json or {}).get(f"d{h}") == "MATURED" for r in rows) for h in HORIZONS}
        data = {"as_of_date": as_of_date, "outcome_count": len(rows), "matured": matured,
                "pending": sum(v == "PENDING" for r in rows for v in (r.horizon_status_json or {}).values()),
                "tradable": sum(r.entry_status == "FILLED" for r in rows), "sample_status": sample_status(matured["d3"]),
                "fair_ab_matured_count": 0, "fair_ab_sample_status": "INSUFFICIENT_SAMPLE",
                "promotion_recommendation": "KEEP_V22_V3_SHADOW", "opaque_llm_contribution": True,
                "external_api_calls": 0, "llm_calls": 0, "orders": 0, "scheduler": False}
        return success_response(data=data, trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/outcomes")
def outcomes(request: Request, as_of_date: date = Query(...), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500)) -> dict:
    init_db(); session = get_session()
    try:
        rows = list(session.scalars(select(ForwardOutcome).where(ForwardOutcome.entry_trade_date <= as_of_date)
                                    .order_by(ForwardOutcome.trade_date.desc(), ForwardOutcome.stock_code)
                                    .offset((page - 1) * page_size).limit(page_size)))
        fields = ("source_run_id", "route", "trade_date", "stock_code", "stock_name", "baseline_status", "v2_2_status", "v3_status",
                  "entry_trade_date", "entry_price", "entry_status", "return_d1", "return_d3", "return_d5", "return_d10",
                  "mae_d1", "mfe_d1", "missed_opportunity", "avoided_loss", "non_fill_quality",
                  "data_status", "horizon_status_json", "binding_gate")
        return success_response(data={"items": [_serialize(x, fields) for x in rows], "page": page, "page_size": page_size}, trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/model-comparison")
def model_comparison(request: Request, as_of_date: date = Query(...)) -> dict:
    init_db(); session = get_session()
    try:
        rows = session.scalars(select(ModelVersionComparison).where(ModelVersionComparison.as_of_date == as_of_date).order_by(ModelVersionComparison.model_route, ModelVersionComparison.segment))
        fields = ("model_route", "segment", "horizon", "sample_count", "fill_count", "positive_rate", "average_return", "median_return", "average_mae", "average_mfe", "profit_factor", "maximum_loss", "sample_status", "fair_sample", "details_json")
        return success_response(data=[_serialize(x, fields) for x in rows], trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/gates")
def gates(request: Request, as_of_date: date = Query(...)) -> dict:
    init_db(); session = get_session()
    try:
        rows = session.scalars(select(GateValueEvaluation).where(GateValueEvaluation.as_of_date == as_of_date).order_by(GateValueEvaluation.gate_name))
        fields = ("gate_name", "gate_scope", "reached_count", "triggered_count", "blocked_count", "unique_blocked_count", "co_blocked_count", "binding_count", "avoided_loss", "missed_gain", "local_net_gate_value", "portfolio_marginal_value", "false_negative_rate", "reject_precision", "evaluation_horizon", "sample_status")
        return success_response(data=[_serialize(x, fields) for x in rows], trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/factors")
def factors(request: Request, as_of_date: date = Query(...)) -> dict:
    init_db(); session = get_session()
    try:
        rows = session.scalars(select(FactorPerformanceHistory).where(FactorPerformanceHistory.period_end == as_of_date).order_by(FactorPerformanceHistory.factor_family))
        fields = ("factor_family", "sample_count", "positive_contribution_count", "negative_contribution_count", "mean_contribution", "median_contribution", "contribution_return_correlation", "contribution_rank_ic", "top_contribution_return", "bottom_contribution_return", "contribution_hit_rate", "avg_return_d1", "avg_return_d3", "avg_return_d5", "avg_drawdown", "average_mfe", "profit_factor")
        return success_response(data=[_serialize(x, fields) for x in rows], trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/pending")
def pending(request: Request, as_of_date: date = Query(...)) -> dict:
    init_db(); session = get_session()
    try:
        rows = list(session.scalars(select(ForwardOutcome).where(ForwardOutcome.entry_trade_date <= as_of_date).order_by(ForwardOutcome.entry_trade_date, ForwardOutcome.stock_code)))
        data = [{"source_run_id": r.source_run_id, "stock_code": r.stock_code, "entry_trade_date": r.entry_trade_date,
                 "pending_horizons": [f"D{h}" for h in HORIZONS if (r.horizon_status_json or {}).get(f"d{h}") == "PENDING"]}
                for r in rows if "PENDING" in (r.horizon_status_json or {}).values()]
        return success_response(data=data, trace_id=request.state.trace_id)
    finally: session.close()
