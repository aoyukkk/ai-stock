from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isclose
from typing import Any, Iterable, Mapping


class ComparisonStatus(StrEnum):
    MATCH = "MATCH"
    MATCH_WITH_TOLERANCE = "MATCH_WITH_TOLERANCE"
    UNIT_CONVERTED_MATCH = "UNIT_CONVERTED_MATCH"
    MATERIAL_CONFLICT = "MATERIAL_CONFLICT"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    SOURCE_MISSING = "SOURCE_MISSING"
    UNIT_UNKNOWN = "UNIT_UNKNOWN"
    TIMESTAMP_MISMATCH = "TIMESTAMP_MISMATCH"


@dataclass(frozen=True)
class IFindTushareComparisonService:
    price_absolute_tolerance: float = 0.01
    price_relative_tolerance: float = 0.0005
    percentage_absolute_tolerance: float = 0.01
    volume_relative_tolerance: float = 0.01
    amount_relative_tolerance: float = 0.01

    def compare(
        self,
        ifind: Mapping[str, Any] | None,
        tushare: Mapping[str, Any] | None,
        *,
        fields: tuple[str, ...] = ("open", "high", "low", "close", "volume", "amount"),
        units: Mapping[str, str] | None = None,
        timestamps_match: bool | None = None,
    ) -> dict[str, Any]:
        if not ifind or not tushare:
            return {"status": ComparisonStatus.SOURCE_MISSING.value, "fields": {}, "conflicts": []}
        if timestamps_match is False:
            return {"status": ComparisonStatus.TIMESTAMP_MISMATCH.value, "fields": {}, "conflicts": []}
        details: dict[str, str] = {}
        conflicts: list[str] = []
        comparable = 0
        tolerance_match = False
        for field in fields:
            if units is not None and units.get(field) in {None, "", "UNKNOWN", "UNIT_UNKNOWN"}:
                details[field] = ComparisonStatus.UNIT_UNKNOWN.value
                continue
            left, right = _number(ifind.get(field)), _number(tushare.get(field))
            if left is None or right is None:
                details[field] = ComparisonStatus.NOT_COMPARABLE.value
                continue
            comparable += 1
            if left == right:
                details[field] = ComparisonStatus.MATCH.value
            elif isclose(left, right, rel_tol=self._relative_tolerance(field), abs_tol=self._absolute_tolerance(field)):
                details[field] = ComparisonStatus.MATCH_WITH_TOLERANCE.value
                tolerance_match = True
            else:
                details[field] = ComparisonStatus.MATERIAL_CONFLICT.value
                conflicts.append(field)
        unknown_units = any(value == ComparisonStatus.UNIT_UNKNOWN.value for value in details.values())
        status = ComparisonStatus.NOT_COMPARABLE.value if not comparable and not unknown_units else ComparisonStatus.MATERIAL_CONFLICT.value if conflicts else ComparisonStatus.UNIT_UNKNOWN.value if unknown_units else ComparisonStatus.MATCH_WITH_TOLERANCE.value if tolerance_match else ComparisonStatus.MATCH.value
        return {"status": status, "fields": details, "conflicts": conflicts, "official_source_unchanged": True}

    def compare_many(self, pairs: Iterable[tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]], **kwargs: Any) -> dict[str, Any]:
        items = [self.compare(ifind, tushare, **kwargs) for ifind, tushare in pairs]
        counts = {status.value: sum(item["status"] == status.value for item in items) for status in ComparisonStatus}
        return {"compared_rows": len(items), "counts": counts, "items": items, "official_source_unchanged": True}

    def _relative_tolerance(self, field: str) -> float:
        if field in {"volume", "vol"}: return self.volume_relative_tolerance
        if field in {"amount", "amt"}: return self.amount_relative_tolerance
        return self.price_relative_tolerance

    def _absolute_tolerance(self, field: str) -> float:
        if field in {"pct_chg", "change_percent"}: return self.percentage_absolute_tolerance
        return self.price_absolute_tolerance


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
