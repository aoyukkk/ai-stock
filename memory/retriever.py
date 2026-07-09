from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select

from database.models.memory import AgentMemoryNote, MemoryRetrievalLog
from database.session import get_session, init_db
from memory.config import MemoryConfig, load_memory_config
from memory.layers import now_utc
from memory.schemas import MemorySearchQuery, MemorySearchResult
from memory.store import note_to_result


class MemoryRetriever:
    def __init__(self, session=None, config: MemoryConfig | None = None) -> None:
        self.session = session
        self.config = config or load_memory_config()

    def search_memories(self, query: MemorySearchQuery) -> MemorySearchResult:
        session, own_session = self._session()
        try:
            candidates = self._fetch_candidates(session, query)
            ranked = sorted(candidates, key=_rank_key, reverse=True)
            top_k = query.top_k or self.config.top_k
            selected = ranked[:top_k]
            retrieval_scores = {str(note.id): float(_rank_score(note)) for note in selected}
            log_id = self._write_retrieval_log(session, query, selected, retrieval_scores)
            return MemorySearchResult(
                notes=[note_to_result(note) for note in selected],
                total=len(ranked),
                retrieval_log_id=log_id,
            )
        finally:
            if own_session:
                session.close()

    def _fetch_candidates(self, session, query: MemorySearchQuery) -> list[AgentMemoryNote]:
        statement = select(AgentMemoryNote)
        if query.agent_name:
            statement = statement.where(AgentMemoryNote.agent_name == query.agent_name)
        if query.stock_code:
            statement = statement.where(AgentMemoryNote.stock_code == query.stock_code)
        if query.memory_type:
            statement = statement.where(AgentMemoryNote.memory_type == query.memory_type)
        if query.layer:
            statement = statement.where(AgentMemoryNote.layer == query.layer)

        include_expired = self.config.include_expired if query.include_expired is None else query.include_expired
        if not include_expired:
            current = now_utc()
            statement = statement.where(or_(AgentMemoryNote.valid_until.is_(None), AgentMemoryNote.valid_until >= current))

        statement = statement.where(
            or_(
                AgentMemoryNote.quality_score.is_(None),
                AgentMemoryNote.quality_score >= self.config.min_quality_score,
            )
        )

        if self.config.pollution_control.get("enabled", True):
            blocked = self.config.blocked_conflict_statuses
            if blocked:
                statement = statement.where(
                    or_(
                        AgentMemoryNote.conflict_status.is_(None),
                        AgentMemoryNote.conflict_status.not_in(blocked),
                    )
                )
            if self.config.require_should_reuse:
                statement = statement.where(AgentMemoryNote.should_reuse.is_(True))

        if query.keyword:
            pattern = f"%{query.keyword}%"
            statement = statement.where(
                or_(
                    AgentMemoryNote.title.ilike(pattern),
                    AgentMemoryNote.content.ilike(pattern),
                    AgentMemoryNote.summary.ilike(pattern),
                )
            )

        return list(session.scalars(statement).all())

    def _write_retrieval_log(
        self,
        session,
        query: MemorySearchQuery,
        notes: list[AgentMemoryNote],
        retrieval_scores: dict[str, float],
    ) -> int | None:
        if not self.config.log_retrieval:
            return None
        log = MemoryRetrievalLog(
            agent_name=query.agent_name,
            task="memory_search",
            stock_code=query.stock_code,
            query_text=query.keyword,
            retrieved_memory_ids=[note.id for note in notes],
            used_memory_ids=[note.id for note in notes],
            retrieval_score_json=retrieval_scores,
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        return log.id

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True


def _rank_key(note: AgentMemoryNote) -> tuple[Decimal, datetime, int]:
    return (_rank_score(note), note.created_at, note.id)


def _rank_score(note: AgentMemoryNote) -> Decimal:
    importance = note.importance or Decimal("50")
    quality = note.quality_score or Decimal("50")
    confidence = note.confidence or Decimal("50")
    return (importance * Decimal("0.35") + quality * Decimal("0.45") + confidence * Decimal("0.20")).quantize(Decimal("0.0001"))
