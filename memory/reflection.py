from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select

from database.models.review import DailyReview
from database.session import get_session, init_db
from llm_gateway.service import LLMGatewayService
from memory.config import MemoryConfig, load_memory_config
from memory.exceptions import MemoryNotFoundError
from memory.quality import reflection_quality_score
from memory.schemas import MemoryNoteCreate, MemoryNoteResult
from memory.store import MemoryStore
from review.persistence import ensure_daily_review_schema


class ReflectionMemoryBuilder:
    def __init__(
        self,
        session=None,
        config: MemoryConfig | None = None,
        store: MemoryStore | None = None,
        llm_gateway: LLMGatewayService | None = None,
    ) -> None:
        self.session = session
        self.config = config or load_memory_config()
        self.store = store or MemoryStore(session=session, config=self.config)
        self.llm_gateway = llm_gateway

    def create_reflection_from_daily_review(self, review_id: int) -> MemoryNoteResult:
        session, own_session = self._session()
        try:
            ensure_daily_review_schema(session)
            review = session.get(DailyReview, review_id)
            if review is None:
                raise MemoryNotFoundError(f"Daily review not found: {review_id}")
            fields = {
                "mistake_analysis": review.mistake_analysis,
                "suggestion": review.suggestion,
                "prediction_accuracy": review.prediction_accuracy,
                "order_price_quality": review.order_price_quality,
                "profit_loss": review.profit_loss,
                "max_drawdown": review.max_drawdown,
                "win_rate": review.win_rate,
            }
            content = _build_content(fields)
            summary = _rule_summary(review)
            if self.config.use_mock_llm_for_reflection:
                summary = self._mock_llm_summary(fields, summary)
            non_empty = len([value for value in fields.values() if value not in (None, "")])
            quality = reflection_quality_score(
                non_empty,
                len(fields),
                base_score=review.final_review_score if hasattr(review, "final_review_score") else None,
            )
            return self.store.create_note(
                MemoryNoteCreate(
                    agent_name="reflection_agent",
                    memory_type="reflection",
                    layer="strategy",
                    title=f"Daily review reflection {review.date.isoformat()}",
                    content=content,
                    summary=summary,
                    importance=Decimal("80"),
                    confidence=Decimal("75"),
                    quality_score=quality,
                    source_type="daily_review",
                    source_id=review.id,
                    metadata={
                        "review_date": review.date.isoformat(),
                        "real_trading_enabled": False,
                        "mock_llm_used": self.config.use_mock_llm_for_reflection,
                    },
                )
            )
        finally:
            if own_session:
                session.close()

    def _mock_llm_summary(self, fields: dict, fallback_summary: str) -> str:
        gateway = self.llm_gateway or LLMGatewayService()
        response = gateway.chat_simple(
            agent_name="memory_reflection_agent",
            task="memory_reflection",
            user_content=json.dumps(fields, ensure_ascii=False, default=str, sort_keys=True),
            metadata={"mock_only": True, "structured": False},
        )
        if response.provider != "mock":
            raise RuntimeError("Reflection memory must use Mock LLM provider in Phase 12.")
        return f"{fallback_summary} Mock LLM summary: {response.content}"

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True


def _build_content(fields: dict) -> str:
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


def _rule_summary(review: DailyReview) -> str:
    return (
        f"Review score={getattr(review, 'final_review_score', None)}; "
        f"prediction_accuracy={review.prediction_accuracy}; "
        f"order_price_quality={review.order_price_quality}; "
        "reuse only as analysis context."
    )
