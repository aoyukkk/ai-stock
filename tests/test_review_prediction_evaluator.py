from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.ai import PredictionRecord
from database.models.review import PredictionEvaluation
from database.session import create_engine_from_url, get_session
from datasource.schemas import KlineBar
from review.prediction_evaluator import PredictionEvaluator


class StubDataSource:
    def __init__(self, bars: list[KlineBar]) -> None:
        self.bars = bars

    def get_kline(self, stock_code, start_date, end_date, frequency="1d"):
        return self.bars


def make_bar(close: str = "10.20") -> KlineBar:
    return KlineBar(
        stock_code="000001",
        trade_date=date(2026, 1, 5),
        open=Decimal("10.00"),
        high=Decimal("10.30"),
        low=Decimal("9.90"),
        close=Decimal(close),
        pre_close=Decimal("10.00"),
        volume=1000000,
        amount=Decimal("10000000"),
    )


def test_prediction_evaluator_compares_expected_and_actual_direction() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        prediction = PredictionRecord(
            stock_code="000001",
            prediction_time=datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc),
            prediction_horizon_days=1,
            expected_direction="UP",
            expected_return=Decimal("2.0000"),
            recommendation="WATCH",
        )
        session.add(prediction)
        session.commit()

        result = PredictionEvaluator(
            session=session,
            data_source_service=StubDataSource([make_bar()]),
        ).evaluate_prediction(prediction.id, date(2026, 1, 5))

        evaluations = session.scalars(select(PredictionEvaluation)).all()
        assert result.actual_direction == "UP"
        assert result.is_correct is True
        assert len(evaluations) == 1
        assert evaluations[0].prediction_record_id == prediction.id
    finally:
        session.close()
        engine.dispose()


def test_prediction_evaluator_handles_missing_market_data() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        prediction = PredictionRecord(
            stock_code="000001",
            prediction_time=datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc),
            prediction_horizon_days=1,
            expected_direction="UP",
        )
        session.add(prediction)
        session.commit()

        result = PredictionEvaluator(
            session=session,
            data_source_service=StubDataSource([]),
        ).evaluate_prediction(prediction.id, date(2026, 1, 5))

        assert result.error_reason
        assert result.is_correct is None
        assert session.scalar(select(PredictionEvaluation)) is not None
    finally:
        session.close()
        engine.dispose()
