from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class PipelineJob(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "pipeline_job"
    __table_args__ = (
        Index("ix_pipeline_job_trade_type", "trade_date", "job_type"),
        Index("ix_pipeline_job_status", "status"),
    )

    job_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="PENDING")
    progress_current: Mapped[int] = mapped_column(default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(default=0, nullable=False)
    current_stock: Mapped[str | None] = mapped_column(String(32))
    success_count: Mapped[int] = mapped_column(default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(default=0, nullable=False)
    token_usage: Mapped[int] = mapped_column(default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(default=0.0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    run_ids: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ManualSelectionRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "manual_selection_record"
    __table_args__ = (
        UniqueConstraint("trade_date", "stock_code", name="uq_manual_selection_trade_stock"),
        Index("ix_manual_selection_trade_date", "trade_date"),
    )

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    quant_run_id: Mapped[str | None] = mapped_column(String(64))
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="MEDIUM")
    selected_by: Mapped[str] = mapped_column(String(128), nullable=False, default="local_trader")


class WorkbenchRunRegistry(IDMixin, TimestampMixin, ReprMixin, Base):
    """Read-only pipeline relationship metadata used by the Workbench."""

    __tablename__ = "workbench_run_registry"
    __table_args__ = (
        UniqueConstraint(
            "trade_date",
            "pipeline_run_id",
            "candidate_set_hash",
            name="uq_workbench_registry_pipeline_candidate",
        ),
        Index("ix_workbench_registry_trade_date", "trade_date", "reconciled_at"),
    )

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    pipeline_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_id: Mapped[str] = mapped_column(String(64), nullable=False)
    flash_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pro_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    order_run_id: Mapped[str | None] = mapped_column(String(128))
    position_run_id: Mapped[str | None] = mapped_column(String(128))
    export_path: Mapped[str | None] = mapped_column(Text)
    export_sha256: Mapped[str | None] = mapped_column(String(64))
    final_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="HISTORICAL_RECONCILIATION")
    counts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    validation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
