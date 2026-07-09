from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from database.models.memory import StrategyPlaybook
from database.models.review import DailyReview
from database.session import get_session, init_db
from memory.exceptions import MemoryNotFoundError
from memory.quality import clamp_score
from memory.schemas import StrategyPlaybookResult
from review.persistence import ensure_daily_review_schema


class StrategyPlaybookService:
    def __init__(self, session=None) -> None:
        self.session = session

    def create_playbook_from_review(self, review_id: int) -> StrategyPlaybookResult:
        session, own_session = self._session()
        try:
            ensure_daily_review_schema(session)
            review = session.get(DailyReview, review_id)
            if review is None:
                raise MemoryNotFoundError(f"Daily review not found: {review_id}")
            name = f"Review playbook {review.date.isoformat()}"
            scenario = _scenario_from_review(review)
            workflow = _workflow_from_review(review)
            quality = clamp_score(review.final_review_score or Decimal("60"))
            playbook = StrategyPlaybook(
                name=name,
                scenario=scenario,
                workflow=workflow,
                quality_score=quality,
                status="ACTIVE",
            )
            session.add(playbook)
            session.commit()
            session.refresh(playbook)
            return playbook_to_result(playbook)
        finally:
            if own_session:
                session.close()

    def list_playbooks(self) -> list[StrategyPlaybookResult]:
        session, own_session = self._session()
        try:
            playbooks = session.scalars(
                select(StrategyPlaybook).order_by(StrategyPlaybook.created_at.desc(), StrategyPlaybook.id.desc())
            ).all()
            return [playbook_to_result(playbook) for playbook in playbooks]
        finally:
            if own_session:
                session.close()

    def update_playbook_quality(self, playbook_id: int, success: bool = True) -> StrategyPlaybookResult:
        session, own_session = self._session()
        try:
            playbook = session.get(StrategyPlaybook, playbook_id)
            if playbook is None:
                raise MemoryNotFoundError(f"Strategy playbook not found: {playbook_id}")
            base = playbook.quality_score or Decimal("60")
            if success:
                playbook.success_count += 1
                playbook.quality_score = clamp_score(base + Decimal("5"))
            else:
                playbook.failure_count += 1
                playbook.quality_score = clamp_score(base - Decimal("5"))
            session.commit()
            session.refresh(playbook)
            return playbook_to_result(playbook)
        finally:
            if own_session:
                session.close()

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True


def playbook_to_result(playbook: StrategyPlaybook) -> StrategyPlaybookResult:
    return StrategyPlaybookResult(
        id=playbook.id,
        name=playbook.name,
        scenario=playbook.scenario,
        workflow=playbook.workflow,
        success_count=playbook.success_count,
        failure_count=playbook.failure_count,
        quality_score=playbook.quality_score,
        status=playbook.status,
        created_at=playbook.created_at,
    )


def _scenario_from_review(review: DailyReview) -> str:
    return (
        "Daily review scenario based on prediction accuracy, order price quality, "
        f"drawdown={review.max_drawdown}, P/L={review.profit_loss}."
    )


def _workflow_from_review(review: DailyReview) -> str:
    return (
        "1. Review wrong prediction directions. "
        "2. Check conservative or aggressive order-price misses. "
        "3. Keep risk-control rules that avoided drawdown. "
        f"4. Next-session suggestion: {review.suggestion or 'continue virtual validation'}"
    )
