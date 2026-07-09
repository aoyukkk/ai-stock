from __future__ import annotations

from decimal import Decimal
from statistics import mean
from typing import Iterable


def clamp_score(value, min_score: float = 0, max_score: float = 100) -> Decimal:
    if value is None:
        return Decimal(str(min_score))
    numeric = Decimal(str(value))
    lower = Decimal(str(min_score))
    upper = Decimal(str(max_score))
    return max(lower, min(upper, numeric)).quantize(Decimal("0.0001"))


def min_max_normalize(
    value,
    min_value,
    max_value,
    higher_is_better: bool = True,
) -> Decimal:
    if value is None or min_value is None or max_value is None:
        return Decimal("50.0000")
    min_decimal = Decimal(str(min_value))
    max_decimal = Decimal(str(max_value))
    value_decimal = Decimal(str(value))
    if max_decimal == min_decimal:
        return Decimal("50.0000")

    ratio = (value_decimal - min_decimal) / (max_decimal - min_decimal)
    if not higher_is_better:
        ratio = Decimal("1") - ratio
    return clamp_score(ratio * Decimal("100"))


def percentile_score(
    value,
    values: Iterable,
    higher_is_better: bool = True,
) -> Decimal:
    clean_values = [Decimal(str(item)) for item in values if item is not None]
    if value is None or not clean_values:
        return Decimal("50.0000")
    if len(set(clean_values)) == 1:
        return Decimal("50.0000")

    value_decimal = Decimal(str(value))
    below_or_equal = sum(1 for item in clean_values if item <= value_decimal)
    score = Decimal(below_or_equal) / Decimal(len(clean_values)) * Decimal("100")
    if not higher_is_better:
        score = Decimal("100") - score
    return clamp_score(score)


def average_score(scores: Iterable) -> Decimal:
    clean_scores = [float(score) for score in scores if score is not None]
    if not clean_scores:
        return Decimal("50.0000")
    return clamp_score(mean(clean_scores))
