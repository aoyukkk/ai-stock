from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


SCORE = Numeric(8, 4)
AMOUNT = Numeric(20, 4)


class LLMUsage(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "llm_usage"
    __table_args__ = (
        Index("ix_llm_usage_provider_model", "provider", "model_name"),
        Index("ix_llm_usage_agent_created", "agent_name", "created_at"),
        Index("ix_llm_usage_created", "created_at"),
    )

    provider: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))
    model_alias: Mapped[str | None] = mapped_column(String(128))
    agent_name: Mapped[str | None] = mapped_column(String(128))
    task: Mapped[str | None] = mapped_column(String(128))
    task_type: Mapped[str | None] = mapped_column(String(128))
    task_tier: Mapped[str | None] = mapped_column(String(32))
    thinking_mode: Mapped[str | None] = mapped_column(String(32))
    reasoning_effort: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(default=0)
    input_cache_hit_tokens: Mapped[int | None] = mapped_column(default=0)
    input_cache_miss_tokens: Mapped[int | None] = mapped_column(default=0)
    output_tokens: Mapped[int | None] = mapped_column(default=0)
    cached_input_tokens: Mapped[int | None] = mapped_column(default=0)
    total_tokens: Mapped[int | None] = mapped_column(default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(AMOUNT)
    cost_status: Mapped[str | None] = mapped_column(String(64))
    pricing_version: Mapped[str | None] = mapped_column(String(128))
    latency_ms: Mapped[int | None] = mapped_column()
    status: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str | None] = mapped_column(String(128))


class AgentExecutionLog(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "agent_execution_log"
    __table_args__ = (
        Index("ix_agent_execution_log_agent_created", "agent_name", "created_at"),
        Index("ix_agent_execution_log_stock_created", "stock_code", "created_at"),
    )

    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    task: Mapped[str | None] = mapped_column(String(128))
    stock_code: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32))
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PromptVersion(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "prompt_version"
    __table_args__ = (
        Index("ix_prompt_version_agent_version", "agent_name", "version"),
        Index("ix_prompt_version_agent_active", "agent_name", "is_active"),
    )

    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ModelVersion(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_version"
    __table_args__ = (
        Index("ix_model_version_provider_model", "provider", "model_name"),
        Index("ix_model_version_alias", "model_alias"),
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_alias: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ConfigHistory(IDMixin, ReprMixin, Base):
    __tablename__ = "config_history"
    __table_args__ = (
        Index("ix_config_history_key", "config_key"),
        Index("ix_config_history_time", "time"),
    )

    user: Mapped[str | None] = mapped_column(String(128))
    config_key: Mapped[str] = mapped_column(String(256), nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SystemConfig(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "system_config"
    __table_args__ = (
        UniqueConstraint("config_key", name="uq_system_config_key"),
        Index("ix_system_config_key", "config_key"),
        Index("ix_system_config_category", "category"),
    )

    config_key: Mapped[str] = mapped_column(String(256), nullable=False)
    config_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    value_type: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    editable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(128))


class ExperimentRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "experiment_run"
    __table_args__ = (
        Index("ix_experiment_run_name", "name"),
        Index("ix_experiment_run_status", "status"),
        Index("ix_experiment_run_date_range", "start_date", "end_date"),
    )

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    strategy_version: Mapped[str | None] = mapped_column(String(64))
    model_config_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    factor_version: Mapped[str | None] = mapped_column(String(64))
    order_price_version: Mapped[str | None] = mapped_column(String(64))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    initial_cash: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(32))
