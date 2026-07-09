from __future__ import annotations

from backend.core.exceptions import AppException
from database.session import get_session, init_db
from memory.config import MemoryConfig, load_memory_config
from memory.exceptions import MemoryNotFoundError, MemoryValidationError
from memory.linker import MemoryLinker
from memory.playbook import StrategyPlaybookService
from memory.reflection import ReflectionMemoryBuilder
from memory.retriever import MemoryRetriever
from memory.schemas import (
    MemoryLinkCreate,
    MemoryLinkResult,
    MemoryNoteCreate,
    MemoryNoteResult,
    MemorySearchQuery,
    MemorySearchResult,
    StrategyPlaybookResult,
)
from memory.store import MemoryStore


class MemoryService:
    def __init__(self, session=None, config: MemoryConfig | None = None) -> None:
        self.session = session
        self.config = config or load_memory_config()

    def create_memory(self, data: MemoryNoteCreate) -> MemoryNoteResult:
        return self._store().create_note(data)

    def get_memory(self, note_id: int) -> MemoryNoteResult:
        note = self._store().get_note(note_id)
        if note is None:
            raise AppException("MEMORY_NOT_FOUND", "Memory note not found.", status_code=404)
        return note

    def search_memory(self, query: MemorySearchQuery) -> MemorySearchResult:
        return MemoryRetriever(session=self.session, config=self.config).search_memories(query)

    def create_link(self, data: MemoryLinkCreate) -> MemoryLinkResult:
        try:
            return self._store().create_link(data)
        except (MemoryNotFoundError, MemoryValidationError) as exc:
            raise AppException("MEMORY_LINK_ERROR", str(exc), status_code=400) from exc

    def build_reflection_from_review(self, review_id: int) -> MemoryNoteResult:
        try:
            return ReflectionMemoryBuilder(session=self.session, config=self.config).create_reflection_from_daily_review(review_id)
        except MemoryNotFoundError as exc:
            raise AppException("DAILY_REVIEW_NOT_FOUND", str(exc), status_code=404) from exc

    def build_playbook_from_review(self, review_id: int) -> StrategyPlaybookResult:
        try:
            return StrategyPlaybookService(session=self.session).create_playbook_from_review(review_id)
        except MemoryNotFoundError as exc:
            raise AppException("DAILY_REVIEW_NOT_FOUND", str(exc), status_code=404) from exc

    def list_playbooks(self) -> list[StrategyPlaybookResult]:
        return StrategyPlaybookService(session=self.session).list_playbooks()

    def disable_memory(self, note_id: int, reason: str | None = None) -> MemoryNoteResult:
        try:
            return self._store().disable_note(note_id, reason=reason)
        except MemoryNotFoundError as exc:
            raise AppException("MEMORY_NOT_FOUND", str(exc), status_code=404) from exc

    def mark_memory_conflict(self, note_id: int, status: str, reason: str | None = None) -> MemoryNoteResult:
        try:
            return self._store().mark_conflict(note_id, status=status, reason=reason)
        except MemoryNotFoundError as exc:
            raise AppException("MEMORY_NOT_FOUND", str(exc), status_code=404) from exc
        except MemoryValidationError as exc:
            raise AppException("MEMORY_VALIDATION_ERROR", str(exc), status_code=400) from exc

    def auto_link_memory(self, note_id: int) -> list[MemoryLinkResult]:
        return MemoryLinker(session=self.session, store=self._store()).auto_link_by_stock_and_type(note_id)

    def config_summary(self) -> dict:
        return self.config.summary()

    def _store(self) -> MemoryStore:
        return MemoryStore(session=self.session, config=self.config)

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True
