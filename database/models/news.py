from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, CreatedAtMixin


class News(Base, CreatedAtMixin):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(100), index=True)
    url: Mapped[str | None] = mapped_column(String(1000))
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    importance: Mapped[int | None] = mapped_column(index=True)
    sentiment: Mapped[str | None] = mapped_column(String(50))
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class NewsStockRelation(Base, CreatedAtMixin):
    __tablename__ = "news_stock_relation"
    __table_args__ = (
        Index("ix_news_stock_relation_news_id_stock_code", "news_id", "stock_code"),
        Index("ix_news_stock_relation_stock_code_impact_score", "stock_code", "impact_score"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    news_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    industry: Mapped[str | None] = mapped_column(String(100))
    impact_direction: Mapped[str | None] = mapped_column(
        String(20),
        comment="Allowed: POSITIVE, NEGATIVE, NEUTRAL, UNKNOWN",
    )
    impact_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), index=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    reason: Mapped[str | None] = mapped_column(Text)
