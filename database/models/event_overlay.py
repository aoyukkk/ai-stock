from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class EventEvidenceSnapshotRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "event_evidence_snapshot"
    __table_args__ = (
        UniqueConstraint("snapshot_id", name="uq_event_evidence_snapshot_id"),
        Index("ix_event_snapshot_run_stock", "run_id", "stock_code"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    decision_as_of_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    search_status: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    direct_search_used: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence_discount_applied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    production_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shadow_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)


class EventEvidenceItemRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "event_evidence_item"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "event_id", name="uq_event_item_snapshot_event"),
        Index("ix_event_item_cluster", "event_cluster_id"),
    )

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("event_evidence_snapshot.id"), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_cluster_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    event_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    materiality: Mapped[float] = mapped_column(nullable=False)
    relevance: Mapped[float] = mapped_column(nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    temporal_status: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(nullable=False, default=1)
    raw_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class EventReviewResultRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "event_review_result"
    __table_args__ = (UniqueConstraint("run_id", "stock_code", name="uq_event_review_run_stock"),)

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("event_evidence_snapshot.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    search_status: Mapped[str] = mapped_column(String(64), nullable=False)
    event_opportunity_score: Mapped[float] = mapped_column(nullable=False)
    evidence_confidence: Mapped[float] = mapped_column(nullable=False)
    breadth_score: Mapped[float] = mapped_column(nullable=False)
    risk_action: Mapped[str] = mapped_column(String(32), nullable=False)
    conflicts_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    warnings_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    review_version: Mapped[str] = mapped_column(String(64), nullable=False)


class EventScreeningRunRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "event_screening_run"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_event_screening_run_id"),
        Index("ix_event_screening_date_version", "trade_date", "screening_version"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    decision_as_of_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    universe_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    factor_version: Mapped[str] = mapped_column(String(96), nullable=False)
    screening_version: Mapped[str] = mapped_column(String(96), nullable=False)
    decision_version: Mapped[str] = mapped_column(String(96), nullable=False)
    production_or_shadow: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    real_search_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    historical_replay: Mapped[bool] = mapped_column(Boolean, nullable=False)
    input_count: Mapped[int] = mapped_column(nullable=False)
    output_count: Mapped[int] = mapped_column(nullable=False)
    actual_network_calls: Mapped[int] = mapped_column(nullable=False)
    logical_evaluations: Mapped[int] = mapped_column(nullable=False)
    reused_checkpoint_count: Mapped[int] = mapped_column(nullable=False)
    stale_checkpoint_count: Mapped[int] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class EventScreeningItemRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "event_screening_item"
    __table_args__ = (
        UniqueConstraint("screening_run_id", "stock_code", name="uq_event_screening_item"),
        Index("ix_event_screening_rank", "screening_run_id", "v3_rank"),
    )

    screening_run_id: Mapped[int] = mapped_column(ForeignKey("event_screening_run.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(128), nullable=False)
    quant_rank: Mapped[int] = mapped_column(nullable=False)
    quant_score: Mapped[float] = mapped_column(nullable=False)
    event_opportunity_score: Mapped[float] = mapped_column(nullable=False)
    evidence_confidence: Mapped[float] = mapped_column(nullable=False)
    evidence_breadth: Mapped[float] = mapped_column(nullable=False)
    risk_action: Mapped[str] = mapped_column(String(32), nullable=False)
    v3_screening_score: Mapped[float] = mapped_column(nullable=False)
    v3_rank: Mapped[int] = mapped_column(nullable=False)
    selected_top20: Mapped[bool] = mapped_column(Boolean, nullable=False)
    search_status: Mapped[str] = mapped_column(String(64), nullable=False)
    event_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_status: Mapped[str] = mapped_column(String(32), nullable=False)
    hard_gate_reasons_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    raw_quant_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class EventOverlayDataIssueRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "event_overlay_data_issue"
    __table_args__ = (UniqueConstraint("issue_hash", name="uq_event_overlay_issue_hash"),)

    issue_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    issue_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str | None] = mapped_column(String(32))
    issue_code: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_level: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _immutable(*_args, **_kwargs) -> None:
    raise ValueError("IMMUTABLE_EVENT_OVERLAY_RECORD")


for _model in (
    EventEvidenceSnapshotRecord,
    EventEvidenceItemRecord,
    EventReviewResultRecord,
    EventScreeningRunRecord,
    EventScreeningItemRecord,
):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
