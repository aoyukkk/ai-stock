from __future__ import annotations

from decimal import Decimal


def virtual_reason(reason: str | None = None) -> str:
    suffix = f": {reason}" if reason else ""
    return f"virtual trading simulation{suffix}"


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"))
