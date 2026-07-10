from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Index, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class AllocationRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "allocation_run"
    __table_args__ = (Index("ix_allocation_run_run_id", "run_id", unique=True),)

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    recommendation_run_id: Mapped[str | None] = mapped_column(String(64))
    account_id: Mapped[str | None] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    advisory_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    total_suggested_capital: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    total_maximum_planned_loss: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    request_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    quant_run_id: Mapped[str | None] = mapped_column(String(64))
    run_data_manifest_id: Mapped[str | None] = mapped_column(String(64))
    order_plan_id: Mapped[int | None] = mapped_column()
    account_snapshot_time: Mapped[str | None] = mapped_column(String(64))


class PositionSuggestionRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "position_suggestion"
    __table_args__ = (Index("ix_position_suggestion_run_stock", "run_id", "stock_code"),)

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    relative_allocation_weight: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    account_position_percent: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    suggested_capital: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    suggested_quantity: Mapped[int] = mapped_column(nullable=False)
    risk_per_share: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    maximum_planned_loss: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    binding_constraint: Mapped[str] = mapped_column(String(64), nullable=False)
    constraint_quantities: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
