from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.memory import AgentMemoryNote
from database.models.review import DailyReview
from database.session import create_engine_from_url, get_session
from memory.reflection import ReflectionMemoryBuilder


def test_reflection_memory_can_be_created_from_daily_review_with_mock_llm() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        review = DailyReview(
            date=date(2026, 1, 5),
            market_summary="mock market",
            ai_summary="mock ai",
            human_summary="No human trading record available.",
            prediction_accuracy=Decimal("80"),
            order_price_quality=Decimal("75"),
            profit_loss=Decimal("100.00"),
            max_drawdown=Decimal("2"),
            win_rate=Decimal("60"),
            mistake_analysis="Missed conservative breakout.",
            suggestion="Tune conservative order-price parameters.",
            final_review_score=Decimal("78"),
            module_scores={"prediction_accuracy": 80},
        )
        session.add(review)
        session.commit()

        result = ReflectionMemoryBuilder(session=session).create_reflection_from_daily_review(review.id)
        persisted = session.scalar(select(AgentMemoryNote).where(AgentMemoryNote.id == result.id))

        assert result.memory_type == "reflection"
        assert result.layer == "strategy"
        assert result.quality_score is not None
        assert "Mock LLM response" in (result.summary or "")
        assert persisted is not None
        assert persisted.source_type == "daily_review"
    finally:
        session.close()
        engine.dispose()
