from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


THS_MEMBER_FIELDS = (
    "ts_code,con_code,con_name,weight,in_date,out_date,is_new"
)


def stock_code(value: Any) -> str:
    return str(value or "").split(".", 1)[0].zfill(6)


def dedupe_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    keys: Sequence[str],
) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    duplicate_count = 0
    for source in rows:
        row = dict(source)
        key = tuple(str(row.get(name) or "") for name in keys)
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        output.append(row)
    return output, duplicate_count


def factor_cache_audit(
    *,
    raw_rows: Sequence[Mapping[str, Any]],
    cached_rows: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    raw, raw_duplicates = dedupe_rows(raw_rows, keys=("ts_code", "trade_date"))
    cached, cached_duplicates = dedupe_rows(
        cached_rows, keys=("ts_code", "trade_date")
    )
    universe = {
        str(row.get("ts_code") or "")
        for row in daily_rows
        if row.get("ts_code")
    }
    raw_codes = {str(row.get("ts_code") or "") for row in raw}
    cached_codes = {str(row.get("ts_code") or "") for row in cached}
    return {
        "raw_rows": len(raw_rows),
        "raw_unique_rows": len(raw),
        "raw_duplicate_rows": raw_duplicates,
        "cache_rows_before_refresh": len(cached_rows),
        "cache_unique_rows_before_refresh": len(cached),
        "cache_duplicate_rows_before_refresh": cached_duplicates,
        "raw_missing_codes": sorted(universe - raw_codes),
        "cache_missing_codes_before_refresh": sorted(universe - cached_codes),
        "partial_cache_confirmed": bool(cached_codes and cached_codes < raw_codes),
    }


def fetch_ths_members_by_board(
    provider: Any,
    boards: Sequence[Mapping[str, Any]],
    *,
    category: str,
    output_root: Path,
    refresh: bool = False,
    progress: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch current THS members one board at a time.

    The endpoint is not treated as a global offset-paginated table.  This
    prevents the duplicate-page runaway that previously produced 300k+ rows.
    """

    category_root = output_root / category
    category_root.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    board_results: list[dict[str, Any]] = []
    failed_boards: list[str] = []
    empty_boards: list[str] = []
    possible_truncation_boards: list[str] = []
    historical_rows_removed = 0

    for index, board in enumerate(boards, 1):
        board_code = str(board.get("ts_code") or "")
        result = provider.query_endpoint(
            "ths_member",
            params={"ts_code": board_code},
            fields=THS_MEMBER_FIELDS,
            use_cache=not refresh,
            write_cache=True,
        )
        raw = list(result.records)
        has_is_new = bool(raw) and all("is_new" in row for row in raw)
        current = [
            dict(row)
            for row in raw
            if not has_is_new or str(row.get("is_new") or "").upper() == "Y"
        ]
        historical_rows_removed += len(raw) - len(current)
        unique, duplicate_count = dedupe_rows(
            current, keys=("ts_code", "con_code")
        )
        payload = {
            "board_code": board_code,
            "board_name": board.get("name"),
            "category": category,
            "status": result.status,
            "error_type": result.error_type,
            "raw_rows": len(raw),
            "current_rows": len(current),
            "unique_rows": len(unique),
            "duplicate_rows": duplicate_count,
            "is_new_available": has_is_new,
            "rows": unique,
        }
        (category_root / f"{board_code}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        board_results.append({key: value for key, value in payload.items() if key != "rows"})
        if result.status != "available":
            failed_boards.append(board_code)
        elif not unique:
            empty_boards.append(board_code)
        if len(raw) >= 5000:
            possible_truncation_boards.append(board_code)
        all_rows.extend(unique)
        if progress and (index == 1 or index % 25 == 0 or index == len(boards)):
            print(
                f"[ths-member] {category} {index}/{len(boards)} "
                f"board={board_code} rows={len(unique)}",
                flush=True,
            )

    unique_all, cross_board_duplicates = dedupe_rows(
        all_rows, keys=("ts_code", "con_code")
    )
    covered_stocks = {
        stock_code(row.get("con_code"))
        for row in unique_all
        if row.get("con_code")
    }
    audit = {
        "category": category,
        "board_count": len(boards),
        "successful_board_count": len(boards) - len(failed_boards),
        "failed_boards": failed_boards,
        "empty_boards": empty_boards,
        "possible_truncation_boards": possible_truncation_boards,
        "raw_rows": sum(int(row["raw_rows"]) for row in board_results),
        "current_rows": sum(int(row["current_rows"]) for row in board_results),
        "unique_rows": len(unique_all),
        "duplicate_rows": sum(int(row["duplicate_rows"]) for row in board_results)
        + cross_board_duplicates,
        "historical_rows_removed": historical_rows_removed,
        "duplicate_page_issue": False,
        "pagination_used": False,
        "covered_stock_count": len(covered_stocks),
        "complete": not failed_boards and not possible_truncation_boards,
        "boards": board_results,
    }
    (output_root / f"{category}_summary.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return unique_all, audit


def classify_optional_factor(
    universe_codes: set[str],
    available_codes: set[str],
) -> dict[str, str]:
    return {
        item: "PRO_CROSSCHECK_AVAILABLE"
        if item in available_codes
        else "PRO_FACTOR_MISSING"
        for item in sorted(universe_codes)
    }
