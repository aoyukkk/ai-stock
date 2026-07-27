from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from backend.core.responses import success_response
from database.models.ranking_evaluation import (
    RankingEvaluationDailyMetric,
    RankingEvaluationDataIssue,
    RankingEvaluationForwardOutcome,
    RankingEvaluationSnapshot,
    RankingEvaluationSnapshotItem,
    RankingEvaluationWeeklyRun,
)
from database.session import get_session, init_db
from services.ranking_evaluation.outcome_backfill_service import OutcomeBackfillService
from services.ranking_evaluation.snapshot_service import RankingSnapshotService
from services.ranking_evaluation.weekly_report_service import RankingWeeklyReportService


router = APIRouter(
    prefix="/api/ranking-evaluation",
    tags=["ranking-forward-effectiveness-shadow"],
)


class CaptureBody(BaseModel):
    trade_date: date
    source_quant_run_id: str
    factor_version: str
    allow_historical_import: bool = False


class RefreshBody(BaseModel):
    as_of_date: date
    factor_version: str | None = None


class WeeklyBody(BaseModel):
    week_ending: date
    factor_version: str


@router.post("/capture")
def capture(request: Request, body: CaptureBody) -> dict:
    init_db()
    session = get_session()
    try:
        data = RankingSnapshotService(session).capture(
            trade_date=body.trade_date,
            source_quant_run_id=body.source_quant_run_id,
            factor_version=body.factor_version,
            allow_historical_import=body.allow_historical_import,
        )
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/refresh-outcomes")
def refresh_outcomes(request: Request, body: RefreshBody) -> dict:
    init_db()
    session = get_session()
    try:
        data = OutcomeBackfillService(session).refresh(
            as_of_date=body.as_of_date,
            factor_version=body.factor_version,
        )
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.post("/run-weekly")
def run_weekly(request: Request, body: WeeklyBody) -> dict:
    init_db()
    session = get_session()
    try:
        data = RankingWeeklyReportService(session).run(
            week_ending=body.week_ending,
            factor_version=body.factor_version,
        )
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/summary")
def summary(
    request: Request,
    factor_version: str = Query(...),
    as_of_date: date = Query(...),
    evaluation_version: str = Query("RANKING_FORWARD_EFFECTIVENESS_V1"),
) -> dict:
    session = get_session()
    try:
        row = session.scalar(
            select(RankingEvaluationWeeklyRun)
            .where(
                RankingEvaluationWeeklyRun.factor_version == factor_version,
                RankingEvaluationWeeklyRun.week_ending <= as_of_date,
                RankingEvaluationWeeklyRun.evaluation_version == evaluation_version,
            )
            .order_by(RankingEvaluationWeeklyRun.week_ending.desc())
        )
        return success_response(
            data=_weekly_dict(row) if row else None,
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


@router.get("/daily-metrics")
def daily_metrics(
    request: Request,
    factor_version: str = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
    horizon: int | None = Query(None),
) -> dict:
    session = get_session()
    try:
        statement = select(RankingEvaluationDailyMetric).where(
            RankingEvaluationDailyMetric.factor_version == factor_version,
            RankingEvaluationDailyMetric.ranking_trade_date >= start_date,
            RankingEvaluationDailyMetric.ranking_trade_date <= end_date,
        )
        if horizon is not None:
            statement = statement.where(
                RankingEvaluationDailyMetric.horizon == horizon
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    RankingEvaluationDailyMetric.ranking_trade_date,
                    RankingEvaluationDailyMetric.horizon,
                )
            )
        )
        return success_response(
            data=[_metric_dict(row) for row in rows],
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


@router.get("/details")
def details(
    request: Request,
    factor_version: str = Query(...),
    ranking_trade_date: date | None = Query(None),
    stock_code: str | None = Query(None),
    horizon: int | None = Query(None),
) -> dict:
    session = get_session()
    try:
        statement = (
            select(
                RankingEvaluationSnapshot,
                RankingEvaluationSnapshotItem,
                RankingEvaluationForwardOutcome,
            )
            .join(
                RankingEvaluationSnapshotItem,
                RankingEvaluationSnapshotItem.snapshot_id
                == RankingEvaluationSnapshot.id,
            )
            .join(
                RankingEvaluationForwardOutcome,
                RankingEvaluationForwardOutcome.snapshot_item_id
                == RankingEvaluationSnapshotItem.id,
                isouter=True,
            )
            .where(RankingEvaluationSnapshot.factor_version == factor_version)
        )
        if ranking_trade_date is not None:
            statement = statement.where(
                RankingEvaluationSnapshot.ranking_trade_date == ranking_trade_date
            )
        if stock_code:
            statement = statement.where(
                RankingEvaluationSnapshotItem.stock_code == stock_code
            )
        if horizon is not None:
            statement = statement.where(
                RankingEvaluationForwardOutcome.horizon == horizon
            )
        rows = session.execute(
            statement.order_by(
                RankingEvaluationSnapshot.ranking_trade_date,
                RankingEvaluationSnapshotItem.original_rank,
                RankingEvaluationForwardOutcome.horizon,
            )
        ).all()
        return success_response(
            data=[
                _detail_dict(snapshot, item, outcome)
                for snapshot, item, outcome in rows
            ],
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


@router.get("/data-quality")
def data_quality(
    request: Request,
    factor_version: str = Query(...),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
) -> dict:
    session = get_session()
    try:
        statement = select(RankingEvaluationDataIssue).where(
            RankingEvaluationDataIssue.affected_version == factor_version
        )
        if start_date:
            statement = statement.where(
                RankingEvaluationDataIssue.affected_date >= start_date
            )
        if end_date:
            statement = statement.where(
                RankingEvaluationDataIssue.affected_date <= end_date
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    RankingEvaluationDataIssue.affected_date,
                    RankingEvaluationDataIssue.issue_code,
                )
            )
        )
        return success_response(
            data=[_issue_dict(row) for row in rows],
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


@router.get("/runs")
def runs(
    request: Request,
    factor_version: str = Query(...),
) -> dict:
    session = get_session()
    try:
        rows = list(
            session.scalars(
                select(RankingEvaluationWeeklyRun)
                .where(
                    RankingEvaluationWeeklyRun.factor_version == factor_version
                )
                .order_by(RankingEvaluationWeeklyRun.week_ending.desc())
            )
        )
        return success_response(
            data=[_weekly_dict(row) for row in rows],
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


def _weekly_dict(row: RankingEvaluationWeeklyRun) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "factor_version": row.factor_version,
        "week_ending": row.week_ending,
        "activation_date": row.activation_date,
        "as_of_trade_date": row.as_of_trade_date,
        "return_basis": row.return_basis,
        "status": row.status,
        "data_status": row.data_status,
        "summary": row.summary_json,
        "report_hash": row.report_hash,
        "artifacts": row.artifact_paths_json,
    }


def _metric_dict(row: RankingEvaluationDailyMetric) -> dict[str, Any]:
    return {
        "ranking_trade_date": row.ranking_trade_date,
        "factor_version": row.factor_version,
        "horizon": row.horizon,
        "return_basis": row.return_basis,
        "valid_sample_count": row.valid_sample_count,
        "missing_sample_count": row.missing_sample_count,
        "coverage_ratio": row.coverage_ratio,
        "rank_ic": row.rank_ic,
        "calculation_status": row.calculation_status,
        "top20_mean_return": row.top20_mean_return,
        "bottom20_mean_return": row.bottom20_mean_return,
        "spread": row.spread,
        "group_returns": row.group_returns_json,
        "monotonicity_pass_count": row.monotonicity_pass_count,
        "monotonicity_label": row.monotonicity_label,
    }


def _detail_dict(snapshot, item, outcome) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "ranking_trade_date": snapshot.ranking_trade_date,
        "factor_version": snapshot.factor_version,
        "stock_code": item.stock_code,
        "stock_name": item.stock_name,
        "original_rank": item.original_rank,
        "quant_score": item.quant_score,
        "original_group": item.original_group,
        "baseline_close": item.baseline_close,
        "row_data_status": item.row_data_status,
        "row_issue_code": item.row_issue_code,
        "horizon": outcome.horizon if outcome else None,
        "due_trade_date": outcome.due_trade_date if outcome else None,
        "future_close": outcome.future_close if outcome else None,
        "return_decimal": outcome.return_decimal if outcome else None,
        "outcome_status": outcome.outcome_status if outcome else None,
        "missing_reason": outcome.missing_reason if outcome else None,
    }


def _issue_dict(row: RankingEvaluationDataIssue) -> dict[str, Any]:
    return {
        "issue_code": row.issue_code,
        "issue_level": row.issue_level,
        "affected_date": row.affected_date,
        "affected_stock": row.affected_stock,
        "affected_version": row.affected_version,
        "detail": row.detail,
        "detected_at": row.detected_at,
    }
