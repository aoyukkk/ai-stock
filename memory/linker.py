from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from database.models.memory import AgentMemoryNote, MemoryLink
from database.session import get_session, init_db
from memory.layers import tokenize
from memory.schemas import MemoryLinkCreate, MemoryLinkResult
from memory.store import MemoryStore, link_to_result


class MemoryLinker:
    def __init__(self, session=None, store: MemoryStore | None = None) -> None:
        self.session = session
        self.store = store or MemoryStore(session=session)

    def link_related_memories(
        self,
        source_id: int,
        target_id: int,
        relation_type: str,
        strength: Decimal,
        reason: str | None = None,
    ) -> MemoryLinkResult:
        return self.store.create_link(
            MemoryLinkCreate(
                source_memory_id=source_id,
                target_memory_id=target_id,
                relation_type=relation_type,
                strength=strength,
                reason=reason,
            )
        )

    def auto_link_by_stock_and_type(self, note_id: int) -> list[MemoryLinkResult]:
        session, own_session = self._session()
        try:
            note = session.get(AgentMemoryNote, note_id)
            if note is None:
                return []
            candidates = session.scalars(
                select(AgentMemoryNote)
                .where(AgentMemoryNote.id != note.id)
                .where(AgentMemoryNote.should_reuse.is_(True))
                .order_by(AgentMemoryNote.created_at.desc(), AgentMemoryNote.id.desc())
            ).all()
            created: list[MemoryLinkResult] = []
            for candidate in candidates:
                score, reasons = _relatedness(note, candidate)
                if score < Decimal("0.50"):
                    continue
                existing = session.scalar(
                    select(MemoryLink).where(
                        MemoryLink.source_memory_id == note.id,
                        MemoryLink.target_memory_id == candidate.id,
                        MemoryLink.relation_type == "RELATED",
                    )
                )
                if existing is not None:
                    created.append(link_to_result(existing))
                    continue
                created.append(
                    self.store.create_link(
                        MemoryLinkCreate(
                            source_memory_id=note.id,
                            target_memory_id=candidate.id,
                            relation_type="RELATED",
                            strength=min(score, Decimal("1.0000")),
                            reason="; ".join(reasons),
                        )
                    )
                )
            return created
        finally:
            if own_session:
                session.close()

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True


def _relatedness(source: AgentMemoryNote, target: AgentMemoryNote) -> tuple[Decimal, list[str]]:
    score = Decimal("0")
    reasons: list[str] = []
    if source.stock_code and source.stock_code == target.stock_code:
        score += Decimal("0.35")
        reasons.append("same stock_code")
    if source.layer and source.layer == target.layer:
        score += Decimal("0.20")
        reasons.append("same layer")
    if source.memory_type == target.memory_type:
        score += Decimal("0.20")
        reasons.append("same memory_type")
    if source.source_type and source.source_type == target.source_type:
        score += Decimal("0.15")
        reasons.append("same source_type")
    overlap = tokenize(source.title) | tokenize(source.summary)
    overlap = overlap.intersection(tokenize(target.title) | tokenize(target.summary))
    if overlap:
        score += Decimal("0.20")
        reasons.append("keyword overlap")
    return score.quantize(Decimal("0.0001")), reasons
