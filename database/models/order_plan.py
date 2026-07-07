from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, CreatedAtMixin, UpdatedAtMixin


class OrderPlan(Base, CreatedAtMixin, UpdatedAtMixin):
    __tablename__ = "order_plan"
    __table_args__ = (
        Index("ix_order_plan_stock_code_plan_date", "stock_code", "plan_date"),
        Index("ix_order_plan_plan_date_status", "plan_date", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    plan_date: Mapped[date | None] = mapped_column(Date, index=True)
    plan_session: Mapped[str | None] = mapped_column(
        String(30),
        comment="Allowed: POST_MARKET, PRE_MARKET, INTRADAY_RECHECK",
    )
    side: Mapped[str | None] = mapped_column(String(10), comment="Allowed: BUY, SELL")
    strategy_type: Mapped[str | None] = mapped_column(
        String(30),
        comment="Allowed: PULLBACK, BALANCED, BREAKOUT, RISK_EXIT, TAKE_PROFIT",
    )
    recommended_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    price_range_low: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    price_range_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    max_acceptable_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    stop_loss_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    take_profit_1_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    take_profit_2_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    suggested_position_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    reason: Mapped[str | None] = mapped_column(Text)
    valid_conditions: Mapped[dict | None] = mapped_column(JSON)
    cancel_conditions: Mapped[dict | None] = mapped_column(JSON)
    reprice_conditions: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str | None] = mapped_column(
        String(20),
        index=True,
        comment="Allowed: DRAFT, ACTIVE, CANCELLED, REPRICED, EXPIRED, EXECUTED, BLOCKED",
    )
    decision_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, index=True)


class OrderPriceCandidate(Base, CreatedAtMixin):
    __tablename__ = "order_price_candidate"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_plan_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    price_type: Mapped[str | None] = mapped_column(
        String(30),
        index=True,
        comment="Allowed: CONSERVATIVE, BALANCED, AGGRESSIVE, BREAKOUT, STOP_LOSS, TAKE_PROFIT",
    )
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), index=True)
    fill_probability: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    expected_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    risk_reward: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    expected_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    stop_loss_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    reason: Mapped[str | None] = mapped_column(Text)


class OrderPlanEvaluation(Base, CreatedAtMixin):
    __tablename__ = "order_plan_evaluation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_plan_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    evaluation_date: Mapped[date | None] = mapped_column(Date, index=True)
    actual_open: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    actual_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    actual_low: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    actual_close: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    actual_volume: Mapped[int | None] = mapped_column(BigInteger)
    was_filled: Mapped[bool | None] = mapped_column()
    simulated_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    best_possible_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    worst_possible_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    price_quality_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), index=True)
    missed_opportunity: Mapped[bool | None] = mapped_column()
    risk_avoided: Mapped[bool | None] = mapped_column()
    evaluation_reason: Mapped[str | None] = mapped_column(Text)


class OrderReassessmentLog(Base, CreatedAtMixin):
    __tablename__ = "order_reassessment_log"
    __table_args__ = (
        Index("ix_order_reassessment_log_order_plan_id_reassess_time", "order_plan_id", "reassess_time"),
        Index("ix_order_reassessment_log_stock_code_reassess_time", "stock_code", "reassess_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_plan_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    reassess_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    trigger_type: Mapped[str | None] = mapped_column(
        String(50),
        comment="Allowed: PRE_MARKET_NEWS, AUCTION_CHANGE, PRICE_MOVE, VOLUME_ABNORMAL, RISK_EVENT, MANUAL_REFRESH",
    )
    old_recommended_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    new_recommended_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    action: Mapped[str | None] = mapped_column(
        String(20),
        comment="Allowed: KEEP, CANCEL, REPRICE, BLOCK",
    )
    reason: Mapped[str | None] = mapped_column(Text)
