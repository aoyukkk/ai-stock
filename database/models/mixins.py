from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IDMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class ReprMixin:
    def __repr__(self) -> str:
        values: list[str] = []
        for key in ("id", "stock_code", "code", "name"):
            value: Any = getattr(self, key, None)
            if value is not None:
                values.append(f"{key}={value!r}")
        joined = ", ".join(values)
        return f"{self.__class__.__name__}({joined})"
