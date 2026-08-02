from __future__ import annotations

from datetime import date
import csv
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from backend.core.responses import success_response
from backend.core.exceptions import AppException
from database.models.ranking_evaluation import (
    ModelEffectivenessDailyMetric,
    ModelEffectivenessDataIssue,
    ModelEffectivenessStageItem,
    ModelEffectivenessStageSnapshot,
    ModelEffectivenessWeeklyRun,
    RankingEvaluationForwardOutcome,
)
from database.models.full_universe_effectiveness import FullUniverseEvaluationRun
from database.session import get_session


router = APIRouter(
    prefix="/api/model-effectiveness",
    tags=["model-effectiveness"],
)


def _versions(
    quant_factor_version: str | None,
    screening_version: str | None,
) -> tuple[str, str]:
    if not quant_factor_version or not screening_version:
        raise AppException(
            "MODEL_VERSION_REQUIRED",
            "quant_factor_version and screening_version are required",
            status_code=400,
        )
    return quant_factor_version, screening_version


@router.get("/summary")
def summary(
    request: Request,
    quant_factor_version: str | None = Query(None),
    screening_version: str | None = Query(None),
    run_id: str | None = Query(None),
) -> dict:
    quant, screen = _versions(quant_factor_version, screening_version)
    session = get_session()
    try:
        statement = select(ModelEffectivenessWeeklyRun).where(
            ModelEffectivenessWeeklyRun.quant_factor_version == quant,
            ModelEffectivenessWeeklyRun.screening_version == screen,
        )
        if run_id:
            statement = statement.where(ModelEffectivenessWeeklyRun.run_id == run_id)
        row = session.scalar(
            statement.order_by(
                ModelEffectivenessWeeklyRun.week_ending.desc(),
                ModelEffectivenessWeeklyRun.id.desc(),
            )
        )
        return success_response(
            data=(row.summary_json if row else {}),
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


@router.get("/quant")
def quant_metrics(
    request: Request,
    quant_factor_version: str | None = Query(None),
    screening_version: str | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    horizon: int | None = Query(None),
) -> dict:
    return _daily_response(
        request, quant_factor_version, screening_version,
        start_date, end_date, horizon, stage_type="QUANT",
    )


@router.get("/flash")
def flash_metrics(
    request: Request,
    quant_factor_version: str | None = Query(None),
    screening_version: str | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    horizon: int | None = Query(None),
    actionable_only: bool = Query(False),
) -> dict:
    return _daily_response(
        request, quant_factor_version, screening_version,
        start_date, end_date, horizon, actionable_only=actionable_only,
    )


@router.get("/incremental-lift")
def incremental_lift(
    request: Request,
    quant_factor_version: str | None = Query(None),
    screening_version: str | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    horizon: int | None = Query(None),
    actionable_only: bool = Query(False),
) -> dict:
    return _daily_response(
        request, quant_factor_version, screening_version,
        start_date, end_date, horizon,
        actionable_only=actionable_only,
        keys=("incremental_lift", "selection_spread", "promote_demote_spread"),
    )


@router.get("/promote-demote")
def promote_demote(
    request: Request,
    quant_factor_version: str | None = Query(None),
    screening_version: str | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    horizon: int | None = Query(None),
) -> dict:
    return _daily_response(
        request, quant_factor_version, screening_version,
        start_date, end_date, horizon,
        keys=(
            "promoted_count", "demoted_count", "overlap_count",
            "promoted_mean_return", "demoted_mean_return", "promote_demote_spread",
        ),
    )


@router.get("/v2-v3-comparison")
def v2_v3(
    request: Request,
    quant_factor_version: str | None = Query(None),
    screening_version: str | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    horizon: int | None = Query(None),
) -> dict:
    return _daily_response(
        request, quant_factor_version, screening_version,
        start_date, end_date, horizon,
    )


@router.get("/daily-metrics")
def daily_metrics(
    request: Request,
    quant_factor_version: str | None = Query(None),
    screening_version: str | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    horizon: int | None = Query(None),
    actionable_only: bool = Query(False),
) -> dict:
    return _daily_response(
        request, quant_factor_version, screening_version,
        start_date, end_date, horizon,
        actionable_only=actionable_only,
    )


@router.get("/details")
def details(
    request: Request,
    quant_factor_version: str | None = Query(None),
    screening_version: str | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    horizon: int | None = Query(None),
    actionable_only: bool = Query(False),
) -> dict:
    quant, screen = _versions(quant_factor_version, screening_version)
    session = get_session()
    try:
        statement = (
            select(
                ModelEffectivenessStageSnapshot,
                ModelEffectivenessStageItem,
                RankingEvaluationForwardOutcome,
            )
            .join(
                ModelEffectivenessStageItem,
                ModelEffectivenessStageItem.stage_snapshot_id
                == ModelEffectivenessStageSnapshot.id,
            )
            .join(
                RankingEvaluationForwardOutcome,
                RankingEvaluationForwardOutcome.snapshot_item_id
                == ModelEffectivenessStageItem.cohort_item_id,
                isouter=True,
            )
            .where(
                ModelEffectivenessStageSnapshot.quant_factor_version == quant,
                ModelEffectivenessStageSnapshot.screening_version == screen,
            )
        )
        if start_date:
            statement = statement.where(
                ModelEffectivenessStageSnapshot.ranking_trade_date >= start_date
            )
        if end_date:
            statement = statement.where(
                ModelEffectivenessStageSnapshot.ranking_trade_date <= end_date
            )
        if horizon:
            statement = statement.where(
                RankingEvaluationForwardOutcome.horizon == horizon
            )
        if actionable_only:
            statement = statement.where(
                ModelEffectivenessStageItem.actionable_before_next_open.is_(True)
            )
        values = []
        for stage, item, outcome in session.execute(statement).all():
            values.append({
                "ranking_trade_date": stage.ranking_trade_date,
                "stage_type": stage.stage_type,
                "stock_code": item.stock_code,
                "stock_name": item.stock_name,
                "quant_rank": item.original_quant_rank,
                "quant_score": item.quant_score,
                "flash_score": item.flash_score,
                "flash_rank": item.flash_rank,
                "selected_flag": item.selected_flag,
                "event_score": item.event_opportunity_score,
                "event_action": item.event_action,
                "risk_action": item.risk_action,
                "horizon": outcome.horizon if outcome else None,
                "due_trade_date": outcome.due_trade_date if outcome else None,
                "future_return": outcome.return_decimal if outcome else None,
                "outcome_status": outcome.outcome_status if outcome else "NOT_MATURED",
                "actionable_before_next_open": item.actionable_before_next_open,
                "data_status": item.data_status,
            })
        return success_response(data=values, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/data-quality")
def data_quality(
    request: Request,
    quant_factor_version: str | None = Query(None),
    screening_version: str | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
) -> dict:
    quant, screen = _versions(quant_factor_version, screening_version)
    session = get_session()
    try:
        statement = select(ModelEffectivenessDataIssue).where(
            ModelEffectivenessDataIssue.quant_factor_version == quant,
            ModelEffectivenessDataIssue.screening_version == screen,
        )
        if start_date:
            statement = statement.where(ModelEffectivenessDataIssue.affected_date >= start_date)
        if end_date:
            statement = statement.where(ModelEffectivenessDataIssue.affected_date <= end_date)
        rows = list(session.scalars(statement.order_by(ModelEffectivenessDataIssue.affected_date)))
        return success_response(
            data=[
                {
                    "issue_code": row.issue_code,
                    "issue_level": row.issue_level,
                    "affected_date": row.affected_date,
                    "affected_stock": row.affected_stock,
                    "detail": row.detail,
                }
                for row in rows
            ],
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


@router.get("/runs")
def runs(
    request: Request,
    quant_factor_version: str | None = Query(None),
    screening_version: str | None = Query(None),
) -> dict:
    quant, screen = _versions(quant_factor_version, screening_version)
    session = get_session()
    try:
        rows = list(
            session.scalars(
                select(ModelEffectivenessWeeklyRun)
                .where(
                    ModelEffectivenessWeeklyRun.quant_factor_version == quant,
                    ModelEffectivenessWeeklyRun.screening_version == screen,
                )
                .order_by(
                    ModelEffectivenessWeeklyRun.week_ending.desc(),
                    ModelEffectivenessWeeklyRun.id.desc(),
                )
            )
        )
        return success_response(
            data=[
                {
                    "run_id": row.run_id,
                    "week_ending": row.week_ending,
                    "status": row.status,
                    "data_status": row.data_status,
                    "report_hash": row.report_hash,
                    "artifacts": row.artifact_paths_json,
                }
                for row in rows
            ],
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


def _daily_response(
    request: Request,
    quant_factor_version: str,
    screening_version: str,
    start_date: date | None,
    end_date: date | None,
    horizon: int | None,
    *,
    stage_type: str | None = None,
    actionable_only: bool = False,
    keys: tuple[str, ...] | None = None,
) -> dict:
    quant, screen = _versions(quant_factor_version, screening_version)
    session = get_session()
    try:
        statement = select(ModelEffectivenessDailyMetric).where(
            ModelEffectivenessDailyMetric.quant_factor_version == quant,
            ModelEffectivenessDailyMetric.actionable_only == actionable_only,
        )
        if stage_type == "QUANT":
            statement = statement.where(
                ModelEffectivenessDailyMetric.stage_type == "QUANT"
            )
        else:
            statement = statement.where(
                ModelEffectivenessDailyMetric.screening_version == screen
            )
        if stage_type:
            statement = statement.where(ModelEffectivenessDailyMetric.stage_type == stage_type)
        if start_date:
            statement = statement.where(ModelEffectivenessDailyMetric.ranking_trade_date >= start_date)
        if end_date:
            statement = statement.where(ModelEffectivenessDailyMetric.ranking_trade_date <= end_date)
        if horizon:
            statement = statement.where(ModelEffectivenessDailyMetric.horizon == horizon)
        rows = list(session.scalars(statement.order_by(
            ModelEffectivenessDailyMetric.ranking_trade_date,
            ModelEffectivenessDailyMetric.horizon,
        )))
        data = []
        for row in rows:
            payload = dict(row.metric_payload_json or {})
            if keys:
                payload = {key: payload.get(key) for key in keys}
            data.append({
                "ranking_trade_date": row.ranking_trade_date,
                "stage_type": row.stage_type,
                "horizon": row.horizon,
                "return_basis": row.return_basis,
                "actionable_only": row.actionable_only,
                "calculation_status": row.calculation_status,
                "valid_sample_count": row.valid_sample_count,
                **payload,
            })
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


def _full_run(
    session,
    *,
    factor_version: str | None,
    evaluation_version: str | None,
    run_id: str | None = None,
) -> FullUniverseEvaluationRun | None:
    if not factor_version or not evaluation_version:
        raise AppException(
            "MODEL_VERSION_REQUIRED",
            "factor_version and evaluation_version are required",
            status_code=400,
        )
    statement = select(FullUniverseEvaluationRun).where(
        FullUniverseEvaluationRun.quant_factor_version == factor_version,
        FullUniverseEvaluationRun.evaluation_version == evaluation_version,
    )
    if run_id:
        statement = statement.where(FullUniverseEvaluationRun.run_id == run_id)
    return session.scalar(
        statement.order_by(
            FullUniverseEvaluationRun.as_of_date.desc(),
            FullUniverseEvaluationRun.id.desc(),
        )
    )


def _artifact_rows(
    row: FullUniverseEvaluationRun | None,
    filename: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    horizon: int | None = None,
    group_scheme: str | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    if row is None:
        return []
    raw_path = (row.artifact_paths_json or {}).get(filename)
    if not raw_path:
        return []
    path = Path(raw_path)
    if not path.exists():
        return []
    values = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for item in csv.DictReader(handle):
            item_date = item.get("ranking_trade_date")
            if start_date and item_date and item_date < start_date.isoformat():
                continue
            if end_date and item_date and item_date > end_date.isoformat():
                continue
            if horizon and str(item.get("horizon")) != str(horizon):
                continue
            if group_scheme and item.get("group_scheme") != group_scheme:
                continue
            values.append(item)
            if len(values) >= limit:
                break
    return values


@router.get("/full-universe/summary")
def full_universe_summary(
    request: Request,
    factor_version: str | None = Query(None),
    evaluation_version: str | None = Query(None),
    run_id: str | None = Query(None),
) -> dict:
    session = get_session()
    try:
        row = _full_run(
            session,
            factor_version=factor_version,
            evaluation_version=evaluation_version,
            run_id=run_id,
        )
        return success_response(
            data=row.summary_json if row else {},
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


def _full_artifact_response(
    request: Request,
    filename: str,
    factor_version: str | None,
    evaluation_version: str | None,
    run_id: str | None,
    start_date: date | None,
    end_date: date | None,
    horizon: int | None,
    group_scheme: str | None = None,
    limit: int = 5000,
) -> dict:
    session = get_session()
    try:
        row = _full_run(
            session,
            factor_version=factor_version,
            evaluation_version=evaluation_version,
            run_id=run_id,
        )
        return success_response(
            data=_artifact_rows(
                row,
                filename,
                start_date=start_date,
                end_date=end_date,
                horizon=horizon,
                group_scheme=group_scheme,
                limit=limit,
            ),
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()


def _full_query_endpoint(filename: str):
    def endpoint(
        request: Request,
        factor_version: str | None = Query(None),
        evaluation_version: str | None = Query(None),
        run_id: str | None = Query(None),
        start_date: date | None = Query(None),
        end_date: date | None = Query(None),
        horizon: int | None = Query(None),
        group_scheme: str | None = Query(None),
    ) -> dict:
        return _full_artifact_response(
            request,
            filename,
            factor_version,
            evaluation_version,
            run_id,
            start_date,
            end_date,
            horizon,
            group_scheme,
        )
    return endpoint


router.add_api_route(
    "/full-universe/ic",
    _full_query_endpoint("full_universe_ic.csv"),
    methods=["GET"],
    name="full_universe_ic",
)
router.add_api_route(
    "/full-universe/deciles",
    _full_query_endpoint("decile_returns.csv"),
    methods=["GET"],
    name="full_universe_deciles",
)
router.add_api_route(
    "/full-universe/fixed-bands",
    _full_query_endpoint("fixed_500_band_returns.csv"),
    methods=["GET"],
    name="full_universe_fixed_bands",
)
router.add_api_route(
    "/full-universe/head-bands",
    _full_query_endpoint("head_band_returns.csv"),
    methods=["GET"],
    name="full_universe_head_bands",
)
router.add_api_route(
    "/full-universe/industry-neutral",
    _full_query_endpoint("industry_excess_metrics.csv"),
    methods=["GET"],
    name="full_universe_industry_neutral",
)
router.add_api_route(
    "/full-universe/factor-ic",
    _full_query_endpoint("factor_score_ic.csv"),
    methods=["GET"],
    name="full_universe_factor_ic",
)
router.add_api_route(
    "/full-universe/details",
    _full_query_endpoint("group_membership.csv"),
    methods=["GET"],
    name="full_universe_details",
)
router.add_api_route(
    "/full-universe/data-quality",
    _full_query_endpoint("data_quality.csv"),
    methods=["GET"],
    name="full_universe_data_quality",
)


@router.get("/full-universe/runs")
def full_universe_runs(
    request: Request,
    factor_version: str | None = Query(None),
    evaluation_version: str | None = Query(None),
) -> dict:
    if not factor_version or not evaluation_version:
        raise AppException(
            "MODEL_VERSION_REQUIRED",
            "factor_version and evaluation_version are required",
            status_code=400,
        )
    session = get_session()
    try:
        rows = list(
            session.scalars(
                select(FullUniverseEvaluationRun)
                .where(
                    FullUniverseEvaluationRun.quant_factor_version == factor_version,
                    FullUniverseEvaluationRun.evaluation_version == evaluation_version,
                )
                .order_by(FullUniverseEvaluationRun.as_of_date.desc())
            )
        )
        return success_response(
            data=[
                {
                    "run_id": row.run_id,
                    "start_date": row.start_date,
                    "end_date": row.end_date,
                    "as_of_date": row.as_of_date,
                    "status": row.status,
                    "conclusion_status": row.conclusion_status,
                    "report_hash": row.report_hash,
                }
                for row in rows
            ],
            trace_id=request.state.trace_id,
        )
    finally:
        session.close()
