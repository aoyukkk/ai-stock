from __future__ import annotations

from decimal import Decimal

import database.models  # noqa: F401
from database.base import Base
from database.session import create_engine_from_url, get_session
from memory.linker import MemoryLinker
from memory.schemas import MemoryNoteCreate
from memory.store import MemoryStore


def make_note(title: str, stock_code: str = "000001") -> MemoryNoteCreate:
    return MemoryNoteCreate(
        agent_name="risk_agent",
        stock_code=stock_code,
        memory_type="reflection",
        layer="strategy",
        title=title,
        content="RiskAgent avoided drawdown in a similar setup.",
        summary=title,
        importance=Decimal("80"),
        confidence=Decimal("75"),
        quality_score=Decimal("85"),
        source_type="daily_review",
    )


def test_memory_linker_can_create_and_auto_link_related_notes() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        store = MemoryStore(session=session)
        first = store.create_note(make_note("risk avoided drawdown"))
        second = store.create_note(make_note("risk avoided chase"))
        store.create_note(make_note("unrelated market note", stock_code="000002"))

        linker = MemoryLinker(session=session, store=store)
        direct = linker.link_related_memories(
            first.id,
            second.id,
            "SUPPORTS",
            Decimal("0.8000"),
            "manual link",
        )
        auto_links = linker.auto_link_by_stock_and_type(first.id)
        all_links = store.list_links(first.id)

        assert direct.relation_type == "SUPPORTS"
        assert any(link.relation_type == "RELATED" for link in auto_links)
        assert len(all_links) >= 2
    finally:
        session.close()
        engine.dispose()
