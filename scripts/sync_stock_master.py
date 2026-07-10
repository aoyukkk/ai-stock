from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.engine import make_url


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.session import get_database_type, get_database_url, get_engine, get_session, init_db
from database.stock_master_sync import ManualStockResolver, StockMasterSyncService


DEFAULT_MANUAL_NAMES = (
    "南方泵业",
    "东岳硅材",
    "冠龙节能",
    "天振股份",
    "朗迪集团",
    "中科曙光",
    "雅克科技",
)


def active_database_identity() -> dict[str, Any]:
    url = make_url(get_database_url())
    engine = get_engine()
    database_name = Path(url.database).name if url.database else None
    sqlite_path = None
    if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
        path = Path(url.database)
        sqlite_path = str((ROOT_DIR / path).resolve() if not path.is_absolute() else path.resolve())
    return {
        "dialect": get_database_type(),
        "database_name": database_name,
        "sqlite_absolute_path": sqlite_path,
        "schema": inspect(engine).default_schema_name,
        "connection_fingerprint": hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:12],
    }


def run_sync(cache_root: Path, manual_names: list[str]) -> dict[str, Any]:
    init_db()
    first = get_session()
    try:
        service = StockMasterSyncService(first, cache_root)
        before = service.row_count()
        sync = service.sync_from_cache(commit=True)
    finally:
        first.close()

    readback = get_session()
    try:
        service = StockMasterSyncService(readback, cache_root)
        after = service.row_count()
        duplicate_count = service.duplicate_code_count()
        resolutions = ManualStockResolver(readback).resolve_all(manual_names)
    finally:
        readback.close()
    return {
        "active_database": active_database_identity(),
        "stock_master_before": before,
        "sync_source": sync.source_path,
        "rows_read": sync.rows_read,
        "rows_inserted": sync.rows_inserted,
        "rows_updated": sync.rows_updated,
        "rows_skipped": sync.rows_skipped,
        "stock_master_after": after,
        "duplicate_count": duplicate_count,
        "manual_stock_resolution": [item.as_dict() for item in resolutions],
        "per_stock_api_call_count": sync.per_stock_api_call_count,
        "llm_calls": sync.llm_call_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Idempotently synchronize stock_master from Tushare stock_basic batch cache.")
    parser.add_argument("--cache-root", type=Path, default=Path("data/cache/tushare/fundamental/stock_basic"))
    parser.add_argument("--manual-names", default=",".join(DEFAULT_MANUAL_NAMES))
    args = parser.parse_args()
    names = [item.strip() for item in args.manual_names.split(",") if item.strip()]
    try:
        result = run_sync(args.cache_root, names)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "reason": f"{type(exc).__name__}:{exc}", "llm_calls": 0}, ensure_ascii=False))
        return 4
    print(json.dumps({"status": "PASS", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
