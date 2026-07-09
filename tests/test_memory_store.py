from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.memory import AgentMemoryNote
from database.session import create_engine_from_url, get_session
from memory.schemas import MemoryNoteCreate
from memory.store import MemoryStore


def make_note(title: str = "Breakout setup") -> MemoryNoteCreate:
    return MemoryNoteCreate(
        agent_name="technical_agent",
        stock_code="000001",
        memory_type="short_term",
        layer="stock",
        title=title,
        content="Mock memory content for a breakout setup.",
        summary="breakout setup",
        importance=Decimal("80"),
        confidence=Decimal("75"),
        quality_score=Decimal("85"),
        source_type="unit_test",
        source_id="case-1",
    )


def test_memory_store_can_create_read_disable_conflict_and_soft_delete() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        store = MemoryStore(session=session)
        created = store.create_note(make_note())
        loaded = store.get_note(created.id)

        assert loaded is not None
        assert loaded.title == "Breakout setup"
        assert loaded.should_reuse is True

        disabled = store.disable_note(created.id, reason="stale setup")
        assert disabled.should_reuse is False
        assert disabled.conflict_status == "INVALIDATED"

        conflict = store.mark_conflict(created.id, "CONFLICTED", reason="contradicted by review")
        assert conflict.conflict_status == "CONFLICTED"
        assert conflict.should_reuse is False

        soft_deleted = store.delete_note(created.id)
        physical = session.scalar(select(AgentMemoryNote).where(AgentMemoryNote.id == created.id))

        assert soft_deleted.should_reuse is False
        assert physical is not None
    finally:
        session.close()
        engine.dispose()


def test_memory_store_creates_and_lists_links() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        store = MemoryStore(session=session)
        first = store.create_note(make_note("Breakout setup A"))
        second = store.create_note(make_note("Breakout setup B"))
        link = store.create_link(
            {
                "source_memory_id": first.id,
                "target_memory_id": second.id,
                "relation_type": "EVIDENCE",
                "strength": Decimal("0.7000"),
                "reason": "same setup",
            }
        )
        links = store.list_links(first.id)

        assert link.relation_type == "EVIDENCE"
        assert len(links) == 1
        assert links[0].target_memory_id == second.id
    finally:
        session.close()
        engine.dispose()
