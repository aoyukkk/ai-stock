from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


MemoryType = Literal[
    "short_term",
    "mid_term",
    "long_term",
    "episodic",
    "reflection",
    "procedural",
    "graph_reference",
]
MemoryLayer = Literal["market", "industry", "stock", "event", "strategy"]
ConflictStatus = Literal["NORMAL", "CONFLICTED", "INVALIDATED", "NEED_REVIEW"]


class MemoryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MemoryNoteCreate(MemoryModel):
    agent_name: str
    stock_code: str | None = None
    memory_type: MemoryType
    layer: MemoryLayer
    title: str
    content: str
    summary: str | None = None
    importance: Decimal = Decimal("50")
    confidence: Decimal = Decimal("50")
    quality_score: Decimal | None = None
    source_type: str | None = None
    source_id: str | int | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryNotePatch(MemoryModel):
    title: str | None = None
    content: str | None = None
    summary: str | None = None
    importance: Decimal | None = None
    confidence: Decimal | None = None
    quality_score: Decimal | None = None
    valid_until: datetime | None = None
    conflict_status: ConflictStatus | None = None
    should_reuse: bool | None = None
    metadata: dict[str, Any] | None = None


class MemoryNoteResult(MemoryModel):
    id: int
    agent_name: str | None = None
    stock_code: str | None = None
    memory_type: str
    layer: str | None = None
    title: str
    summary: str | None = None
    importance: Decimal | None = None
    confidence: Decimal | None = None
    quality_score: Decimal | None = None
    valid_until: datetime | None = None
    conflict_status: str | None = None
    should_reuse: bool
    created_at: datetime


class MemorySearchQuery(MemoryModel):
    agent_name: str | None = None
    stock_code: str | None = None
    memory_type: MemoryType | None = None
    layer: MemoryLayer | None = None
    keyword: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=100)
    include_expired: bool | None = None


class MemorySearchResult(MemoryModel):
    notes: list[MemoryNoteResult]
    total: int
    retrieval_log_id: int | None = None


class MemoryLinkCreate(MemoryModel):
    source_memory_id: int
    target_memory_id: int
    relation_type: str
    strength: Decimal = Decimal("0.5000")
    reason: str | None = None


class MemoryLinkResult(MemoryModel):
    id: int
    source_memory_id: int
    target_memory_id: int
    relation_type: str
    strength: Decimal | None = None
    reason: str | None = None
    created_at: datetime


class StrategyPlaybookCreate(MemoryModel):
    name: str
    scenario: str
    workflow: str
    quality_score: Decimal = Decimal("60")
    status: str = "ACTIVE"


class StrategyPlaybookResult(MemoryModel):
    id: int
    name: str
    scenario: str | None = None
    workflow: str | None = None
    success_count: int
    failure_count: int
    quality_score: Decimal | None = None
    status: str | None = None
    created_at: datetime


class DisableMemoryRequest(MemoryModel):
    reason: str | None = None


class ConflictMemoryRequest(MemoryModel):
    status: ConflictStatus
    reason: str | None = None
