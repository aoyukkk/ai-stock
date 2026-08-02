from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Query, Request

from backend.core.responses import success_response
from database.session import get_session, init_db
from quant.explainability.service import DecisionExplainabilityShadowService, SHADOW_CONFIG
from quant.explainability.factor_performance import FactorPerformanceService
from quant.explainability.gate_evaluation_service import GateEvaluationService


router = APIRouter(
    prefix="/api/workbench/decision-explainability",
    tags=["decision-explainability-shadow"],
)


def _session():
    init_db()
    return get_session()


@router.get("/latest")
def latest_decision_explainability(request: Request, trade_date: date = Query(...)) -> dict:
    session = _session()
    try:
        data = DecisionExplainabilityShadowService(session).latest(trade_date)
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/results")
def decision_explainability_results(
    request: Request,
    run_id: str = Query(..., min_length=1, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    admission_state: Literal["PASS_CORE", "PASS_EXPLORATORY", "REVIEW", "REJECT"] | None = Query(default=None),
    strategy_status: Literal["PROBABILISTIC", "OPEN_SET"] | None = Query(default=None),
) -> dict:
    session = _session()
    try:
        data = DecisionExplainabilityShadowService(session).results(
            run_id,
            page=page,
            page_size=page_size,
            admission_state=admission_state,
            strategy_status=strategy_status,
        )
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/detail")
def decision_explainability_detail(
    request: Request,
    run_id: str = Query(..., min_length=1, max_length=64),
    stock_code: str = Query(..., min_length=6, max_length=32),
) -> dict:
    session = _session()
    try:
        data = DecisionExplainabilityShadowService(session).detail(run_id, stock_code)
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/gates")
def decision_explainability_gates(
    request: Request,
    run_id: str = Query(..., min_length=1, max_length=64),
) -> dict:
    session = _session()
    try:
        data = DecisionExplainabilityShadowService(session).gates(run_id)
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/factor-performance/latest")
def latest_factor_performance(request: Request, end_date: date = Query(...)) -> dict:
    session = _session()
    try:
        data = FactorPerformanceService(session).latest(end_date)
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/gate-ranking")
def gate_value_ranking(
    request: Request,
    end_date: date = Query(...),
    period_days: int = Query(default=60, ge=1, le=3650),
) -> dict:
    session = _session()
    try:
        data = GateEvaluationService(session).evaluate(end_date - timedelta(days=period_days), end_date)
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/methodology")
def decision_explainability_methodology(request: Request) -> dict:
    data = {
        **SHADOW_CONFIG,
        "timing_contract": "observation_end_ts <= available_at_ts <= signal_generated_at < order_eligible_at",
        "invalid_timing_behavior": "INVALID_TIMING_CONTRACT; forward-return calculation is not invoked",
        "hard_safety_gates": ["FUTURE_DATA", "SUSPENDED", "ST", "UNTRADEABLE", "BLACK_SWAN"],
        "admission_states": ["PASS_CORE", "PASS_EXPLORATORY", "REVIEW", "REJECT"],
        "strategy_probability_keys": ["TREND_BREAKOUT", "STRONG_PULLBACK", "SECTOR_RESONANCE", "OVERSOLD_REBOUND", "OPEN_SET"],
        "ev_fields": ["expected_value_score", "risk_adjusted_opportunity_score"],
        "display_only": True,
    }
    return success_response(data=data, trace_id=request.state.trace_id)
