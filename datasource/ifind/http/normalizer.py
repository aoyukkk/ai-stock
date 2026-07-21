from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from stock_codes import normalize_ts_code


SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class IFindNormalizedSummary:
    row_count: int
    returned_fields: tuple[str, ...]
    provider_timestamp: str | None
    local_observation_time: str
    latest_data_time: str | None
    data_status: str


def normalize_probe_response(payload: dict[str, Any], *, observed_at: datetime | None = None) -> IFindNormalizedSummary:
    observation = (observed_at or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    rows = normalize_rows(payload)
    fields = tuple(sorted({str(key) for row in rows for key in row}))
    timestamps = [value for row in rows for key, value in row.items() if _time_key(key) and isinstance(value, str)]
    provider_timestamp = max(timestamps, default=None)
    data_status = _data_status(observation, provider_timestamp)
    return IFindNormalizedSummary(
        row_count=len(rows), returned_fields=fields,
        provider_timestamp=provider_timestamp,
        local_observation_time=observation.isoformat(),
        latest_data_time=provider_timestamp,
        data_status=data_status,
    )


def normalize_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows, _ = normalize_rows_with_audit(payload)
    return rows


def normalize_rows_with_audit(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize every response table and expose a value-free structural audit.

    iFinD uses more than one table representation.  In particular, larger
    responses may pair a security-code array with an array of per-security
    tables.  The old walker treated those nested tables as opaque values and
    could therefore lose codes even when the service returned them.
    """
    branches: list[str] = []
    rows = [_normalize_mapping(row) for row in _rows(payload, branches)]
    return rows, {
        "response_shape": _shape(payload),
        "parser_branch_used": list(dict.fromkeys(branches)) or ["NO_SUPPORTED_TABLE"],
        "raw_security_code_array": _raw_codes(payload),
        "raw_field_array_lengths": _array_lengths(payload),
        "raw_table_count": _table_count(payload),
    }


def _rows(payload: dict[str, Any], branches: list[str] | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    used = branches if branches is not None else []

    def expand_columns(columns: dict[str, list[Any]], metadata: dict[str, Any], branch: str) -> None:
        row_count = max((len(item) for item in columns.values()), default=0)
        if not row_count:
            return
        used.append(branch)
        for index in range(row_count):
            row = dict(metadata)
            nested: list[Any] = []
            for key, items in columns.items():
                item = items[index] if index < len(items) else None
                if isinstance(item, dict):
                    nested.append(item)
                else:
                    row[key] = item
            if nested:
                for item in nested:
                    nested_rows = rows_from_value(item, row, f"{branch}_NESTED")
                    candidates.extend(nested_rows or [row | item])
            else:
                candidates.append(row)

    def rows_from_value(value: Any, inherited: dict[str, Any], branch: str) -> list[dict[str, Any]]:
        before = len(candidates)
        visit(value, inherited, branch)
        extracted = candidates[before:]
        if extracted:
            del candidates[before:]
        return extracted

    def visit(value: Any, inherited: dict[str, Any] | None = None, branch: str = "ROOT") -> None:
        inherited = inherited or {}
        if isinstance(value, list):
            for item in value:
                visit(item, inherited, f"{branch}_LIST")
        elif isinstance(value, dict):
            scalar_metadata = inherited | {
                str(key): item for key, item in value.items()
                if not isinstance(item, (dict, list))
            }
            table = value.get("table")
            if isinstance(table, dict):
                columns = {
                    str(key): item for key, item in value.items()
                    if key != "table" and isinstance(item, list)
                } | {str(key): item for key, item in table.items() if isinstance(item, list)}
                if columns:
                    expand_columns(columns, scalar_metadata, "TABLE_DICT_COLUMNAR")
                    return
                visit(table, scalar_metadata, "TABLE_DICT")
                return
            if isinstance(table, list):
                companion = {
                    str(key): item for key, item in value.items()
                    if key != "table" and isinstance(item, list) and len(item) == len(table)
                }
                used.append("PAIRED_CODE_TABLE_ARRAY")
                for index, item in enumerate(table):
                    metadata = dict(scalar_metadata)
                    for key, items in companion.items():
                        paired = items[index]
                        if not isinstance(paired, (dict, list)):
                            metadata[key] = paired
                    visit(item, metadata, "PAIRED_CODE_TABLE_ARRAY")
                return
            columns = {str(key): item for key, item in value.items() if isinstance(item, list)}
            has_code_context = any(str(key).lower() in {"code", "thscode", "ts_code"} for key in value) or any(
                str(key).lower() in {"code", "thscode", "ts_code"} for key in inherited
            )
            if columns and (len(columns) == len(value) or has_code_context):
                expand_columns(columns, scalar_metadata, "COLUMNAR_MAPPING")
                return
            if value and all(not isinstance(item, (dict, list)) for item in value.values()):
                used.append("ROW_MAPPING")
                candidates.append(inherited | value)
            else:
                for key, item in value.items():
                    if isinstance(item, (dict, list)):
                        visit(item, scalar_metadata, f"{branch}_{str(key).upper()}")

    for key in ("data", "tables", "table", "result"):
        if key in payload:
            visit(payload[key], {}, key.upper())
    return candidates


def _raw_codes(payload: Any) -> list[str]:
    output: list[str] = []

    def visit(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, item in value.items():
                visit(item, str(child_key))
        elif isinstance(value, list):
            for item in value:
                visit(item, key)
        elif key and key.lower() in {"code", "thscode", "ts_code"} and value not in (None, ""):
            try:
                output.append(normalize_ts_code(str(value)))
            except ValueError:
                output.append(str(value))

    visit(payload)
    return output


def _array_lengths(payload: Any) -> dict[str, list[int]]:
    output: dict[str, list[int]] = {}

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            output.setdefault(path, []).append(len(value))
            for index, item in enumerate(value):
                if isinstance(item, (dict, list)):
                    visit(item, f"{path}[]")

    visit(payload, "")
    return output


def _table_count(payload: Any) -> int:
    count = 0

    def visit(value: Any, key: str | None = None) -> None:
        nonlocal count
        if isinstance(value, dict):
            if key in {"table", "tables"} or "table" in value:
                count += 1
            for child_key, item in value.items():
                visit(item, str(child_key))
        elif isinstance(value, list):
            for item in value:
                visit(item, key)

    visit(payload)
    return count


def _shape(value: Any, depth: int = 0) -> Any:
    if depth >= 5:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(key): _shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        samples = value[:2]
        return {"type": "list", "length": len(value), "items": [_shape(item, depth + 1) for item in samples]}
    return type(value).__name__


def _normalize_mapping(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        normalized = _safe_value(value)
        if key.lower() in {"code", "thscode", "ts_code"} and isinstance(normalized, str):
            try:
                normalized = normalize_ts_code(normalized)
            except ValueError:
                pass
        result[str(key)] = normalized
    return result


def _safe_value(value: Any) -> Any:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=SHANGHAI)
        return value.astimezone(SHANGHAI).isoformat()
    return value


def _time_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("time", "date", "datetime", "trade_date"))


def _data_status(observation: datetime, provider_timestamp: str | None) -> str:
    if not provider_timestamp:
        return "TIME_UNKNOWN"
    try:
        provider_time = datetime.fromisoformat(provider_timestamp.replace("Z", "+00:00"))
        if provider_time.tzinfo is None:
            provider_time = provider_time.replace(tzinfo=SHANGHAI)
        provider_time = provider_time.astimezone(SHANGHAI)
    except ValueError:
        return "TIME_UNKNOWN"
    if (
        observation.weekday() < 5
        and observation.date() == provider_time.date()
        and observation.hour >= 15
        and provider_time.hour >= 15
    ):
        return "CLOSED_SESSION_FINAL"
    return "AVAILABLE_DELAYED"
