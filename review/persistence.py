from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect, select, text

from database.models.order_plan import OrderPlanEvaluation
from database.models.review import DailyReview, PredictionEvaluation
from review.schemas import (
    DailyReviewResult,
    OrderPlanEvaluationResult,
    PredictionEvaluationResult,
)


def persist_prediction_evaluation(session, result: PredictionEvaluationResult) -> PredictionEvaluation:
    evaluation = PredictionEvaluation(
        prediction_record_id=result.prediction_record_id,
        stock_code=result.stock_code,
        evaluation_date=result.evaluation_date,
        actual_return=result.actual_return,
        actual_max_drawdown=result.actual_max_drawdown,
        actual_direction=result.actual_direction,
        is_correct=result.is_correct,
        error_reason=result.error_reason,
    )
    session.add(evaluation)
    session.commit()
    session.refresh(evaluation)
    return evaluation


def persist_order_plan_evaluation(session, result: OrderPlanEvaluationResult, actual_volume: int | None = None) -> OrderPlanEvaluation:
    evaluation = OrderPlanEvaluation(
        order_plan_id=result.order_plan_id,
        stock_code=result.stock_code,
        evaluation_date=result.evaluation_date,
        actual_open=result.actual_open,
        actual_high=result.actual_high,
        actual_low=result.actual_low,
        actual_close=result.actual_close,
        actual_volume=actual_volume,
        was_filled=result.was_filled,
        simulated_fill_price=result.simulated_fill_price,
        best_possible_price=result.best_possible_price,
        worst_possible_price=result.worst_possible_price,
        price_quality_score=result.price_quality_score,
        missed_opportunity=result.missed_opportunity,
        risk_avoided=result.risk_avoided,
        evaluation_reason=result.evaluation_reason,
    )
    session.add(evaluation)
    session.commit()
    session.refresh(evaluation)
    return evaluation


def persist_daily_review(session, result: DailyReviewResult) -> DailyReview:
    ensure_daily_review_schema(session)
    review = DailyReview(
        date=result.date,
        account_id=result.account_id,
        market_summary=result.market_summary,
        ai_summary=result.ai_summary,
        human_summary=result.human_summary,
        prediction_accuracy=result.prediction_accuracy,
        order_price_quality=result.order_price_quality,
        profit_loss=result.profit_loss,
        max_drawdown=result.max_drawdown,
        win_rate=result.win_rate,
        mistake_analysis=result.mistake_analysis,
        suggestion=result.suggestion,
        final_review_score=result.final_review_score,
        module_scores=_json_safe_module_scores(result.module_scores),
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def get_latest_daily_review(session, review_date: date) -> DailyReview | None:
    ensure_daily_review_schema(session)
    return session.scalar(
        select(DailyReview)
        .where(DailyReview.date == review_date)
        .order_by(DailyReview.created_at.desc(), DailyReview.id.desc())
    )


def daily_review_to_result(review: DailyReview) -> DailyReviewResult:
    module_scores = {
        key: Decimal(str(value))
        for key, value in (review.module_scores or {}).items()
    }
    return DailyReviewResult(
        date=review.date,
        account_id=review.account_id,
        market_summary=review.market_summary or "",
        ai_summary=review.ai_summary or "",
        human_summary=review.human_summary or "",
        prediction_accuracy=review.prediction_accuracy or Decimal("0"),
        order_price_quality=review.order_price_quality or Decimal("0"),
        profit_loss=review.profit_loss or Decimal("0"),
        max_drawdown=review.max_drawdown or Decimal("0"),
        win_rate=review.win_rate or Decimal("0"),
        mistake_analysis=review.mistake_analysis or "",
        suggestion=review.suggestion or "",
        final_review_score=review.final_review_score or Decimal("0"),
        module_scores=module_scores,
        created_at=review.created_at,
    )


def ensure_daily_review_schema(session) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        return
    inspector = inspect(bind)
    if not inspector.has_table("daily_review"):
        return
    columns = {column["name"] for column in inspector.get_columns("daily_review")}
    additions = {
        "final_review_score": "NUMERIC(8, 4)",
        "module_scores": "JSON",
    }
    for column_name, column_type in additions.items():
        if column_name not in columns:
            session.execute(text(f"ALTER TABLE daily_review ADD COLUMN {column_name} {column_type}"))
    session.commit()


def _json_safe_module_scores(module_scores: dict[str, Decimal]) -> dict[str, Any]:
    return {key: float(value) for key, value in module_scores.items()}
