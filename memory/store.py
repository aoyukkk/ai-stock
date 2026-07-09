from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select

from database.models.memory import AgentMemoryNote, MemoryLink
from database.session import get_session, init_db
from memory.config import MemoryConfig, load_memory_config
from memory.exceptions import MemoryNotFoundError, MemoryValidationError
from memory.layers import CONFLICT_STATUSES, default_valid_until, now_utc
from memory.quality import calculate_quality_score, clamp_score
from memory.schemas import (
    MemoryLinkCreate,
    MemoryLinkResult,
    MemoryNoteCreate,
    MemoryNotePatch,
    MemoryNoteResult,
)


class MemoryStore:
    def __init__(self, session=None, config: MemoryConfig | None = None) -> None:
        self.session = session
        self.config = config or load_memory_config()

    def create_note(self, data: MemoryNoteCreate) -> MemoryNoteResult:
        session, own_session = self._session()
        try:
            note = AgentMemoryNote(
                agent_name=data.agent_name,
                stock_code=data.stock_code,
                memory_type=data.memory_type,
                layer=data.layer,
                title=data.title,
                content=data.content,
                summary=data.summary,
                importance=clamp_score(data.importance),
                confidence=clamp_score(data.confidence),
                quality_score=calculate_quality_score(data),
                source_type=data.source_type,
                source_id=str(data.source_id) if data.source_id is not None else None,
                valid_from=data.valid_from or now_utc(),
                valid_until=data.valid_until or default_valid_until(data.memory_type, self.config),
                conflict_status="NORMAL",
                should_reuse=True,
                metadata_json=dict(data.metadata or {}),
            )
            session.add(note)
            session.commit()
            session.refresh(note)
            return note_to_result(note)
        finally:
            if own_session:
                session.close()

    def get_note(self, note_id: int) -> MemoryNoteResult | None:
        session, own_session = self._session()
        try:
            note = session.get(AgentMemoryNote, note_id)
            return note_to_result(note) if note is not None else None
        finally:
            if own_session:
                session.close()

    def get_note_or_raise(self, note_id: int) -> AgentMemoryNote:
        session, own_session = self._session()
        try:
            note = session.get(AgentMemoryNote, note_id)
            if note is None:
                raise MemoryNotFoundError(f"Memory note not found: {note_id}")
            return note
        finally:
            if own_session:
                session.close()

    def update_note(self, note_id: int, patch: MemoryNotePatch | dict[str, Any]) -> MemoryNoteResult:
        patch_data = patch.model_dump(exclude_unset=True) if isinstance(patch, MemoryNotePatch) else dict(patch)
        session, own_session = self._session()
        try:
            note = _get_note(session, note_id)
            for key, value in patch_data.items():
                if key == "metadata":
                    note.metadata_json = _merge_metadata(note.metadata_json, value or {})
                elif key == "quality_score" and value is not None:
                    note.quality_score = clamp_score(Decimal(str(value)))
                elif key in {"importance", "confidence"} and value is not None:
                    setattr(note, key, clamp_score(Decimal(str(value))))
                elif hasattr(note, key):
                    setattr(note, key, value)
            session.commit()
            session.refresh(note)
            return note_to_result(note)
        finally:
            if own_session:
                session.close()

    def disable_note(self, note_id: int, reason: str | None = None) -> MemoryNoteResult:
        session, own_session = self._session()
        try:
            note = _get_note(session, note_id)
            note.should_reuse = False
            note.conflict_status = "INVALIDATED"
            note.metadata_json = _merge_metadata(
                note.metadata_json,
                {"disabled_reason": reason or "disabled", "disabled_at": now_utc().isoformat()},
            )
            session.commit()
            session.refresh(note)
            return note_to_result(note)
        finally:
            if own_session:
                session.close()

    def mark_conflict(self, note_id: int, status: str, reason: str | None = None) -> MemoryNoteResult:
        normalized = status.upper()
        if normalized not in CONFLICT_STATUSES:
            raise MemoryValidationError(f"Unsupported conflict_status: {status}")
        session, own_session = self._session()
        try:
            note = _get_note(session, note_id)
            note.conflict_status = normalized
            if normalized in {"CONFLICTED", "INVALIDATED"}:
                note.should_reuse = False
            note.metadata_json = _merge_metadata(
                note.metadata_json,
                {"conflict_reason": reason or normalized, "conflict_marked_at": now_utc().isoformat()},
            )
            session.commit()
            session.refresh(note)
            return note_to_result(note)
        finally:
            if own_session:
                session.close()

    def delete_note(self, note_id: int) -> MemoryNoteResult:
        return self.disable_note(note_id, reason="soft delete")

    def list_notes(self, filters: dict[str, Any] | None = None) -> list[MemoryNoteResult]:
        filters = filters or {}
        session, own_session = self._session()
        try:
            query = select(AgentMemoryNote)
            for attr in ("agent_name", "stock_code", "memory_type", "layer", "source_type"):
                value = filters.get(attr)
                if value:
                    query = query.where(getattr(AgentMemoryNote, attr) == value)
            if filters.get("should_reuse") is not None:
                query = query.where(AgentMemoryNote.should_reuse.is_(bool(filters["should_reuse"])))
            if not filters.get("include_expired", False):
                current = now_utc()
                query = query.where(or_(AgentMemoryNote.valid_until.is_(None), AgentMemoryNote.valid_until >= current))
            query = query.order_by(AgentMemoryNote.created_at.desc(), AgentMemoryNote.id.desc())
            return [note_to_result(note) for note in session.scalars(query).all()]
        finally:
            if own_session:
                session.close()

    def create_link(self, data: MemoryLinkCreate | dict[str, Any]) -> MemoryLinkResult:
        if isinstance(data, dict):
            data = MemoryLinkCreate(**data)
        if data.source_memory_id == data.target_memory_id:
            raise MemoryValidationError("A memory note cannot link to itself.")
        session, own_session = self._session()
        try:
            _get_note(session, data.source_memory_id)
            _get_note(session, data.target_memory_id)
            existing = session.scalar(
                select(MemoryLink).where(
                    MemoryLink.source_memory_id == data.source_memory_id,
                    MemoryLink.target_memory_id == data.target_memory_id,
                    MemoryLink.relation_type == data.relation_type,
                )
            )
            if existing is not None:
                return link_to_result(existing)
            link = MemoryLink(
                source_memory_id=data.source_memory_id,
                target_memory_id=data.target_memory_id,
                relation_type=data.relation_type,
                strength=clamp_strength(data.strength),
                reason=data.reason,
            )
            session.add(link)
            session.commit()
            session.refresh(link)
            return link_to_result(link)
        finally:
            if own_session:
                session.close()

    def list_links(self, note_id: int) -> list[MemoryLinkResult]:
        session, own_session = self._session()
        try:
            _get_note(session, note_id)
            links = session.scalars(
                select(MemoryLink)
                .where(
                    or_(
                        MemoryLink.source_memory_id == note_id,
                        MemoryLink.target_memory_id == note_id,
                    )
                )
                .order_by(MemoryLink.created_at.desc(), MemoryLink.id.desc())
            ).all()
            return [link_to_result(link) for link in links]
        finally:
            if own_session:
                session.close()

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True


def note_to_result(note: AgentMemoryNote) -> MemoryNoteResult:
    return MemoryNoteResult(
        id=note.id,
        agent_name=note.agent_name,
        stock_code=note.stock_code,
        memory_type=note.memory_type,
        layer=note.layer,
        title=note.title,
        summary=note.summary,
        importance=note.importance,
        confidence=note.confidence,
        quality_score=note.quality_score,
        valid_until=note.valid_until,
        conflict_status=note.conflict_status or "NORMAL",
        should_reuse=note.should_reuse,
        created_at=note.created_at,
    )


def link_to_result(link: MemoryLink) -> MemoryLinkResult:
    return MemoryLinkResult(
        id=link.id,
        source_memory_id=link.source_memory_id,
        target_memory_id=link.target_memory_id,
        relation_type=link.relation_type,
        strength=link.strength,
        reason=link.reason,
        created_at=link.created_at,
    )


def clamp_strength(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("1"), value)).quantize(Decimal("0.0001"))


def _get_note(session, note_id: int) -> AgentMemoryNote:
    note = session.get(AgentMemoryNote, note_id)
    if note is None:
        raise MemoryNotFoundError(f"Memory note not found: {note_id}")
    return note


def _merge_metadata(existing: dict | None, patch: dict) -> dict:
    merged = dict(existing or {})
    merged.update(patch)
    return merged
