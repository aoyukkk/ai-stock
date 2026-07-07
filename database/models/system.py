from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, CreatedAtMixin, UpdatedAtMixin


class PromptVersion(Base, CreatedAtMixin):
    __tablename__ = "prompt_version"
    __table_args__ = (
        Index("ix_prompt_version_agent_name_version", "agent_name", "version"),
        Index("ix_prompt_version_agent_name_is_active", "agent_name", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent_name: Mapped[str | None] = mapped_column(String(100), index=True)
    version: Mapped[str | None] = mapped_column(String(50), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool | None] = mapped_column(index=True)


class ModelVersion(Base, CreatedAtMixin):
    __tablename__ = "model_version"
    __table_args__ = (Index("ix_model_version_provider_model_name", "provider", "model_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str | None] = mapped_column(String(100), index=True)
    model_name: Mapped[str | None] = mapped_column(String(100), index=True)
    model_alias: Mapped[str | None] = mapped_column(String(100), index=True)
    version: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool | None] = mapped_column(index=True)


class AgentMemory(Base, CreatedAtMixin, UpdatedAtMixin):
    __tablename__ = "agent_memory"
    __table_args__ = (Index("ix_agent_memory_agent_stock_code", "agent", "stock_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent: Mapped[str | None] = mapped_column(String(100), index=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    memory_type: Mapped[str | None] = mapped_column(
        String(30),
        index=True,
        comment="Allowed: short, episodic, long_term, graph_reference",
    )
    content: Mapped[str | None] = mapped_column(Text)
    importance: Mapped[int | None] = mapped_column()
    embedding_id: Mapped[str | None] = mapped_column(String(100))
    source_type: Mapped[str | None] = mapped_column(String(50))
    source_id: Mapped[str | None] = mapped_column(String(100))


class ConfigHistory(Base):
    __tablename__ = "config_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user: Mapped[str | None] = mapped_column(String(100))
    config_key: Mapped[str | None] = mapped_column(String(200), index=True)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
