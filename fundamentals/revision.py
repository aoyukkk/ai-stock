from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_available_at(row: dict[str, Any], interface: str = "formal") -> datetime | None:
    keys = ("ann_date",) if interface in {"forecast", "express"} else ("f_ann_date", "ann_date")
    for key in keys:
        value = str(row.get(key) or "")[:8]
        if len(value) == 8 and value.isdigit():
            return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
    return None


def select_latest_revisions(
    records: list[dict[str, Any]],
    decision_time: datetime,
    interface: str = "formal",
) -> list[dict[str, Any]]:
    if decision_time.tzinfo is None:
        decision_time = decision_time.replace(tzinfo=timezone.utc)
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for original in records:
        row = dict(original)
        available_at = parse_available_at(row, interface)
        if available_at is None or available_at > decision_time:
            continue
        row["available_at"] = available_at.isoformat()
        key = (str(row.get("ts_code", "")), str(row.get("end_date", "")), str(row.get("report_type", "")))
        current = selected.get(key)
        sort_key = (available_at, int(str(row.get("update_flag") or "0") == "1"))
        if current is None or sort_key > (
            datetime.fromisoformat(current["available_at"]),
            int(str(current.get("update_flag") or "0") == "1"),
        ):
            selected[key] = row
    return list(selected.values())


def prefer_financial_publication(
    formal: dict[str, Any] | None,
    express: dict[str, Any] | None,
    forecast: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]] | None:
    if formal:
        return "FORMAL", formal
    if express:
        return "EXPRESS", express
    if forecast:
        return "FORECAST", forecast
    return None
