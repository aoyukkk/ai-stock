from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from database.models.ai import PredictionRecord
from database.session import get_session, init_db
from datasource.service import DataSourceService
from review.config import ReviewConfig, load_review_config
from review.persistence import persist_prediction_evaluation
from review.schemas import PredictionEvaluationResult


DIRECTION_UP_THRESHOLD = Decimal("0.50")
DIRECTION_DOWN_THRESHOLD = Decimal("-0.50")


class PredictionEvaluator:
    def __init__(
        self,
        session=None,
        data_source_service: DataSourceService | None = None,
        config: ReviewConfig | None = None,
    ) -> None:
        self.session = session
        self.data_source_service = data_source_service or DataSourceService()
        self.config = config or load_review_config()

    def evaluate_prediction(self, prediction_record_id: int, evaluation_date: date) -> PredictionEvaluationResult:
        session, own_session = self._session()
        try:
            record = session.get(PredictionRecord, prediction_record_id)
            if record is None:
                return PredictionEvaluationResult(
                    prediction_record_id=prediction_record_id,
                    stock_code="",
                    evaluation_date=evaluation_date,
                    error_reason="Prediction record not found.",
                )

            bars = self.data_source_service.get_kline(record.stock_code, evaluation_date, evaluation_date, "1d")
            if not bars:
                result = PredictionEvaluationResult(
                    prediction_record_id=record.id,
                    stock_code=record.stock_code,
                    evaluation_date=evaluation_date,
                    expected_direction=record.expected_direction,
                    expected_return=record.expected_return,
                    error_reason="No mock market data available for evaluation date.",
                )
                persist_prediction_evaluation(session, result)
                return result

            bar = bars[-1]
            base_price = bar.pre_close or bar.open
            if base_price <= 0:
                actual_return = Decimal("0")
                max_drawdown = Decimal("0")
            else:
                actual_return = _pct((bar.close - base_price) / base_price)
                max_drawdown = _pct((bar.low - base_price) / base_price)

            actual_direction = actual_direction_from_return(actual_return)
            expected_direction = normalize_expected_direction(record.expected_direction)
            is_correct = expected_direction == actual_direction if expected_direction else None

            result = PredictionEvaluationResult(
                prediction_record_id=record.id,
                stock_code=record.stock_code,
                evaluation_date=evaluation_date,
                expected_direction=record.expected_direction,
                actual_direction=actual_direction,
                expected_return=record.expected_return,
                actual_return=actual_return,
                actual_max_drawdown=max_drawdown,
                is_correct=is_correct,
                error_reason=None if is_correct is not None else "Expected direction is unavailable.",
            )
            persist_prediction_evaluation(session, result)
            return result
        finally:
            if own_session:
                session.close()

    def evaluate_predictions_for_date(self, evaluation_date: date) -> list[PredictionEvaluationResult]:
        session, own_session = self._session()
        try:
            records = session.scalars(select(PredictionRecord).order_by(PredictionRecord.id)).all()
            target_records = [
                record
                for record in records
                if _record_matches_evaluation_date(record, evaluation_date, self.config.prediction_horizon_days)
            ]
            return [self.evaluate_prediction(record.id, evaluation_date) for record in target_records]
        finally:
            if own_session:
                session.close()

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True


def actual_direction_from_return(actual_return: Decimal) -> str:
    if actual_return > DIRECTION_UP_THRESHOLD:
        return "UP"
    if actual_return < DIRECTION_DOWN_THRESHOLD:
        return "DOWN"
    return "SIDEWAYS"


def normalize_expected_direction(direction: str | None) -> str | None:
    if direction is None:
        return None
    normalized = direction.strip().upper()
    if normalized in {"UP", "BUY", "LONG", "WATCH"}:
        return "UP"
    if normalized in {"DOWN", "SELL", "AVOID", "SHORT"}:
        return "DOWN"
    if normalized in {"SIDEWAYS", "FLAT", "NEUTRAL"}:
        return "SIDEWAYS"
    return normalized if normalized in {"UP", "DOWN", "SIDEWAYS"} else None


def _record_matches_evaluation_date(record: PredictionRecord, evaluation_date: date, default_horizon_days: int) -> bool:
    prediction_date = record.prediction_time.date()
    horizon = record.prediction_horizon_days or default_horizon_days
    return prediction_date <= evaluation_date <= prediction_date + timedelta(days=horizon)


def _pct(value: Decimal) -> Decimal:
    return (value * Decimal("100")).quantize(Decimal("0.0001"))
