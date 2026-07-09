from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


SCORE = Numeric(8, 4)


class AgentMemoryNote(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "agent_memory_note"
    __table_args__ = (
        Index("ix_agent_memory_note_agent_stock", "agent_name", "stock_code"),
        Index("ix_agent_memory_note_type", "memory_type"),
        Index("ix_agent_memory_note_layer", "layer"),
        Index("ix_agent_memory_note_valid_until", "valid_until"),
        Index("ix_agent_memory_note_quality", "quality_score"),
    )

    agent_name: Mapped[str | None] = mapped_column(String(128))
    stock_code: Mapped[str | None] = mapped_column(String(32))
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    layer: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    importance: Mapped[Decimal | None] = mapped_column(SCORE)
    confidence: Mapped[Decimal | None] = mapped_column(SCORE)
    quality_score: Mapped[Decimal | None] = mapped_column(SCORE)
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(128))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conflict_status: Mapped[str | None] = mapped_column(String(32))
    should_reuse: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    embedding_id: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class MemoryLink(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "memory_link"
    __table_args__ = (
        Index("ix_memory_link_source", "source_memory_id"),
        Index("ix_memory_link_target", "target_memory_id"),
        Index("ix_memory_link_relation", "relation_type"),
    )

    source_memory_id: Mapped[int] = mapped_column(ForeignKey("agent_memory_note.id"), nullable=False)
    target_memory_id: Mapped[int] = mapped_column(ForeignKey("agent_memory_note.id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    strength: Mapped[Decimal | None] = mapped_column(SCORE)
    reason: Mapped[str | None] = mapped_column(Text)


class MemoryRetrievalLog(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "memory_retrieval_log"

    agent_name: Mapped[str | None] = mapped_column(String(128))
    task: Mapped[str | None] = mapped_column(String(128))
    stock_code: Mapped[str | None] = mapped_column(String(32))
    query_text: Mapped[str | None] = mapped_column(Text)
    retrieved_memory_ids: Mapped[list | None] = mapped_column(JSON)
    used_memory_ids: Mapped[list | None] = mapped_column(JSON)
    retrieval_score_json: Mapped[dict | None] = mapped_column(JSON)


class StrategyPlaybook(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "strategy_playbook"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    scenario: Mapped[str | None] = mapped_column(Text)
    workflow: Mapped[str | None] = mapped_column(Text)
    success_count: Mapped[int] = mapped_column(default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(default=0, nullable=False)
    quality_score: Mapped[Decimal | None] = mapped_column(SCORE)
    status: Mapped[str | None] = mapped_column(String(32))
