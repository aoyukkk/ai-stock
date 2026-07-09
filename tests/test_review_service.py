from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.ai import PredictionRecord
from database.models.order_plan import OrderPlan, OrderPlanEvaluation
from database.models.review import DailyReview, PredictionEvaluation
from database.models.trading import Position, TradeOrder, TradingAccount
from database.session import create_engine_from_url, get_session
from review.service import DailyReviewService


def test_run_daily_review_writes_review_and_evaluations_with_mock_llm() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        account = TradingAccount(
            name="AI Simulation",
            type="AI_SIMULATION",
            cash=Decimal("999000.00"),
            total_asset=Decimal("1000000.00"),
            initial_cash=Decimal("1000000.00"),
        )
        session.add(account)
        session.flush()
        session.add(
            Position(
                account_id=account.id,
                stock_code="000001",
                quantity=100,
                cost_price=Decimal("8.00"),
                available_quantity=100,
                buy_date=date(2026, 1, 5),
            )
        )
        session.add(
            TradeOrder(
                account_id=account.id,
                stock_code="000001",
                action="BUY",
                order_price=Decimal("8.00"),
                order_quantity=100,
                filled_quantity=100,
                status="FILLED",
                submit_time=datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc),
            )
        )
        plan = OrderPlan(
            stock_code="000001",
            plan_date=date(2026, 1, 5),
            side="BUY",
            strategy_type="BALANCED",
            recommended_price=Decimal("8.00"),
            status="DRAFT",
        )
        session.add(plan)
        session.flush()
        prediction = PredictionRecord(
            stock_code="000001",
            prediction_time=datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc),
            prediction_horizon_days=1,
            expected_direction="DOWN",
            expected_return=Decimal("-1.0000"),
            recommendation="WATCH",
            order_plan_id=plan.id,
        )
        session.add(prediction)
        session.commit()

        result = DailyReviewService(session=session).run_daily_review(
            review_date=date(2026, 1, 5),
            account_id=account.id,
            use_mock_llm=True,
        )

        assert result.final_review_score >= Decimal("0")
        assert "Mock LLM response" in result.ai_summary
        assert session.scalar(select(DailyReview)) is not None
        assert session.scalar(select(PredictionEvaluation)) is not None
        assert session.scalar(select(OrderPlanEvaluation)) is not None
    finally:
        session.close()
        engine.dispose()
