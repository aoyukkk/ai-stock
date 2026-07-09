from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class News(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "news"
    __table_args__ = (
        Index("ix_news_publish_time", "publish_time"),
        Index("ix_news_source", "source"),
        Index("ix_news_importance", "importance"),
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(1024))
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    importance: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    sentiment: Mapped[str | None] = mapped_column(String(32))
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class NewsStockRelation(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "news_stock_relation"
    __table_args__ = (
        Index("ix_news_stock_relation_news_stock", "news_id", "stock_code"),
        Index("ix_news_stock_relation_stock_impact", "stock_code", "impact_score"),
    )

    news_id: Mapped[int] = mapped_column(ForeignKey("news.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(128))
    impact_direction: Mapped[str | None] = mapped_column(String(32))
    impact_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    reason: Mapped[str | None] = mapped_column(Text)
