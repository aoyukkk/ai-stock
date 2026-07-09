from __future__ import annotations

from datetime import date
from decimal import Decimal

import database.models  # noqa: F401
from database.base import Base
from database.models.review import DailyReview
from database.session import create_engine_from_url, get_session
from memory.playbook import StrategyPlaybookService


def test_playbook_can_be_created_listed_and_updated_from_review() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        review = DailyReview(
            date=date(2026, 1, 5),
            market_summary="mock market",
            ai_summary="mock ai",
            mistake_analysis="RiskAgent avoided drawdown.",
            suggestion="Keep risk gate active.",
            final_review_score=Decimal("70"),
        )
        session.add(review)
        session.commit()

        service = StrategyPlaybookService(session=session)
        playbook = service.create_playbook_from_review(review.id)
        listed = service.list_playbooks()
        updated = service.update_playbook_quality(playbook.id, success=True)

        assert playbook.name.startswith("Review playbook")
        assert len(listed) == 1
        assert updated.success_count == 1
        assert updated.quality_score == Decimal("75.0000")
    finally:
        session.close()
        engine.dispose()
