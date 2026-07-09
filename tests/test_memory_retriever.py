from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.memory import MemoryRetrievalLog
from database.session import create_engine_from_url, get_session
from memory.layers import now_utc
from memory.retriever import MemoryRetriever
from memory.schemas import MemoryNoteCreate, MemorySearchQuery
from memory.store import MemoryStore


def note(
    title: str,
    stock_code: str = "000001",
    memory_type: str = "short_term",
    quality_score: str = "80",
    valid_until=None,
) -> MemoryNoteCreate:
    return MemoryNoteCreate(
        agent_name="technical_agent",
        stock_code=stock_code,
        memory_type=memory_type,  # type: ignore[arg-type]
        layer="stock",
        title=title,
        content=f"{title} mock content",
        summary=title,
        importance=Decimal("80"),
        confidence=Decimal("80"),
        quality_score=Decimal(quality_score),
        source_type="unit_test",
        valid_until=valid_until,
    )


def test_memory_retriever_filters_and_logs_searches() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        store = MemoryStore(session=session)
        keep = store.create_note(note("breakout quality memory"))
        store.create_note(note("other stock memory", stock_code="000002"))
        store.create_note(note("expired breakout memory", valid_until=now_utc() - timedelta(hours=1)))
        store.create_note(note("low quality breakout memory", quality_score="10"))
        conflicted = store.create_note(note("conflicted breakout memory"))
        store.mark_conflict(conflicted.id, "CONFLICTED", reason="test conflict")

        result = MemoryRetriever(session=session).search_memories(
            MemorySearchQuery(stock_code="000001", keyword="breakout")
        )
        logs = session.scalars(select(MemoryRetrievalLog)).all()

        assert [item.id for item in result.notes] == [keep.id]
        assert result.retrieval_log_id is not None
        assert len(logs) == 1
        assert logs[0].retrieved_memory_ids == [keep.id]
    finally:
        session.close()
        engine.dispose()


def test_memory_retriever_filters_by_memory_type_and_can_include_expired() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        store = MemoryStore(session=session)
        store.create_note(note("short setup", memory_type="short_term"))
        mid = store.create_note(note("mid setup", memory_type="mid_term", valid_until=now_utc() - timedelta(hours=1)))

        hidden = MemoryRetriever(session=session).search_memories(
            MemorySearchQuery(memory_type="mid_term")
        )
        visible = MemoryRetriever(session=session).search_memories(
            MemorySearchQuery(memory_type="mid_term", include_expired=True)
        )

        assert hidden.notes == []
        assert [item.id for item in visible.notes] == [mid.id]
    finally:
        session.close()
        engine.dispose()
