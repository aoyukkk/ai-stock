from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


PRICE = Numeric(12, 4)
SCORE = Numeric(8, 4)


class OrderPlan(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "order_plan"
    __table_args__ = (
        Index("ix_order_plan_stock_date", "stock_code", "plan_date"),
        Index("ix_order_plan_date_status", "plan_date", "status"),
        Index("ix_order_plan_decision_snapshot", "decision_snapshot_id"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    plan_session: Mapped[str | None] = mapped_column(String(32))
    side: Mapped[str | None] = mapped_column(String(16))
    strategy_type: Mapped[str | None] = mapped_column(String(64))
    recommended_price: Mapped[Decimal | None] = mapped_column(PRICE)
    price_range_low: Mapped[Decimal | None] = mapped_column(PRICE)
    price_range_high: Mapped[Decimal | None] = mapped_column(PRICE)
    max_acceptable_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_loss_price: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit_1_price: Mapped[Decimal | None] = mapped_column(PRICE)
    take_profit_2_price: Mapped[Decimal | None] = mapped_column(PRICE)
    suggested_position_percent: Mapped[Decimal | None] = mapped_column(SCORE)
    confidence: Mapped[Decimal | None] = mapped_column(SCORE)
    reason: Mapped[str | None] = mapped_column(Text)
    valid_conditions: Mapped[dict | None] = mapped_column(JSON)
    cancel_conditions: Mapped[dict | None] = mapped_column(JSON)
    reprice_conditions: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str | None] = mapped_column(String(32))
    decision_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("decision_snapshot.id"))


class OrderPriceCandidate(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "order_price_candidate"
    __table_args__ = (
        Index("ix_order_price_candidate_order_plan", "order_plan_id"),
        Index("ix_order_price_candidate_type_score", "price_type", "score"),
    )

    order_plan_id: Mapped[int] = mapped_column(ForeignKey("order_plan.id"), nullable=False)
    price_type: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(PRICE)
    score: Mapped[Decimal | None] = mapped_column(SCORE)
    fill_probability: Mapped[Decimal | None] = mapped_column(SCORE)
    expected_return: Mapped[Decimal | None] = mapped_column(SCORE)
    risk_reward: Mapped[Decimal | None] = mapped_column(SCORE)
    expected_profit_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_loss_price: Mapped[Decimal | None] = mapped_column(PRICE)
    reason: Mapped[str | None] = mapped_column(Text)


class OrderPlanEvaluation(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "order_plan_evaluation"
    __table_args__ = (
        Index("ix_order_plan_evaluation_order_plan", "order_plan_id"),
        Index("ix_order_plan_evaluation_stock_date", "stock_code", "evaluation_date"),
        Index("ix_order_plan_evaluation_quality", "price_quality_score"),
    )

    order_plan_id: Mapped[int] = mapped_column(ForeignKey("order_plan.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluation_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_open: Mapped[Decimal | None] = mapped_column(PRICE)
    actual_high: Mapped[Decimal | None] = mapped_column(PRICE)
    actual_low: Mapped[Decimal | None] = mapped_column(PRICE)
    actual_close: Mapped[Decimal | None] = mapped_column(PRICE)
    actual_volume: Mapped[int | None] = mapped_column(BigInteger)
    was_filled: Mapped[bool | None] = mapped_column(Boolean)
    simulated_fill_price: Mapped[Decimal | None] = mapped_column(PRICE)
    best_possible_price: Mapped[Decimal | None] = mapped_column(PRICE)
    worst_possible_price: Mapped[Decimal | None] = mapped_column(PRICE)
    price_quality_score: Mapped[Decimal | None] = mapped_column(SCORE)
    missed_opportunity: Mapped[bool | None] = mapped_column(Boolean)
    risk_avoided: Mapped[bool | None] = mapped_column(Boolean)
    evaluation_reason: Mapped[str | None] = mapped_column(Text)


class OrderReassessmentLog(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "order_reassessment_log"
    __table_args__ = (
        Index("ix_order_reassessment_log_order_time", "order_plan_id", "reassess_time"),
        Index("ix_order_reassessment_log_stock_time", "stock_code", "reassess_time"),
    )

    order_plan_id: Mapped[int] = mapped_column(ForeignKey("order_plan.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reassess_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trigger_type: Mapped[str | None] = mapped_column(String(64))
    old_recommended_price: Mapped[Decimal | None] = mapped_column(PRICE)
    new_recommended_price: Mapped[Decimal | None] = mapped_column(PRICE)
    action: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
