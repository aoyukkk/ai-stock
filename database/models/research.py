from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class FundamentalResearchRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "fundamental_research_run"
    __table_args__ = (Index("ix_fundamental_research_run_run_id", "run_id", unique=True),)

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    stock_codes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    query_count: Mapped[int] = mapped_column(default=0, nullable=False)
    source_count: Mapped[int] = mapped_column(default=0, nullable=False)
    capability_metadata: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ResearchEvidenceRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "research_evidence"
    __table_args__ = (
        Index("ix_research_evidence_run_stock", "run_id", "stock_code"),
        Index("ix_research_evidence_hash", "content_hash"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    credibility_score: Mapped[float] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    related_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class StockFundamentalProfile(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "stock_fundamental_profile"
    __table_args__ = (
        UniqueConstraint("stock_code", "version", name="uq_stock_fundamental_profile_version"),
        Index("ix_stock_fundamental_profile_stock_version", "stock_code", "version"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    research_run_id: Mapped[str | None] = mapped_column(String(64))
    profile: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    field_evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    conflicts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    verified_evidence_count: Mapped[int] = mapped_column(default=0, nullable=False)
    suitable_for_score_boost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    field_provenance_map: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PendingVerificationTask(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "pending_verification_task"
    __table_args__ = (
        UniqueConstraint("stock_code", "field_name", "status", name="uq_pending_verification_open"),
        Index("ix_pending_verification_status_priority", "status", "priority"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    unverified_value: Mapped[dict | str | list | None] = mapped_column(JSON)
    priority: Mapped[int] = mapped_column(default=50, nullable=False)
    source_status: Mapped[str] = mapped_column(String(32), default="LLM_UNVERIFIED", nullable=False)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferred_source_types: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    verified_evidence_id: Mapped[int | None] = mapped_column()
