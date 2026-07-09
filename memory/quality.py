from __future__ import annotations

from decimal import Decimal

from memory.schemas import MemoryNoteCreate


def calculate_quality_score(data: MemoryNoteCreate) -> Decimal:
    if data.quality_score is not None:
        return clamp_score(data.quality_score)

    content_length = len(data.content.strip())
    completeness = Decimal("0")
    for value in (data.title, data.content, data.summary, data.source_type):
        if value:
            completeness += Decimal("5")
    length_bonus = min(Decimal("15"), Decimal(content_length) / Decimal("40"))
    score = (
        data.importance * Decimal("0.35")
        + data.confidence * Decimal("0.35")
        + Decimal("20")
        + completeness
        + length_bonus
    )
    return clamp_score(score)


def reflection_quality_score(non_empty_fields: int, total_fields: int, base_score: Decimal | None = None) -> Decimal:
    completeness = Decimal(non_empty_fields) / Decimal(max(total_fields, 1)) * Decimal("100")
    if base_score is not None:
        return clamp_score((completeness * Decimal("0.60")) + (base_score * Decimal("0.40")))
    return clamp_score(completeness)


def clamp_score(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), value)).quantize(Decimal("0.0001"))
