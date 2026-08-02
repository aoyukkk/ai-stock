"""Publish a consistent, read-only snapshot for the Internal Web service.

Never copy an active SQLite database/WAL pair.  SQLite's backup API reads a
consistent snapshot and the completed temporary file is atomically renamed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_TABLES = (
    "quant_run", "quant_rank_result", "postclose_official_run",
    "midday_recommendation_run", "market_review_run", "forward_outcome",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically publish Internal Web business results")
    parser.add_argument("--source", type=Path, default=Path("data/ai_trader_dev.db"))
    parser.add_argument("--destination", type=Path,
                        default=Path(os.environ.get("INTERNAL_WEB_BUSINESS_DATABASE_PATH",
                            r"C:\ProgramData\AITraderAssistant\business\ai_trader_business.db")))
    args = parser.parse_args()
    summary = publish_snapshot(args.source, args.destination)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def publish_snapshot(source: Path, destination: Path, *, replace_attempts: int = 8) -> dict:
    source, destination = source.expanduser().resolve(), destination.expanduser().resolve()
    if not source.is_file():
        raise RuntimeError("BUSINESS_SOURCE_DATABASE_NOT_FOUND")
    if source == destination:
        raise RuntimeError("PUBLISH_SOURCE_AND_DESTINATION_MUST_DIFFER")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".ai_trader_business_", suffix=".db", dir=destination.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(temp)
        try:
            src.backup(dst)
        finally:
            # sqlite connection context managers commit/rollback but do not
            # close handles; Windows cannot atomically replace an open file.
            dst.close()
            src.close()
        summary = _verify(temp)
        last_error: OSError | None = None
        for attempt in range(1, max(1, replace_attempts) + 1):
            try:
                os.replace(temp, destination)
                last_error = None
                break
            except OSError as exc:
                last_error = exc
                if attempt >= replace_attempts:
                    raise
                time.sleep(min(0.1 * attempt, 0.5))
        if last_error is not None:
            raise last_error
        summary.update({"published": True, "destination_hash": _sha256(destination),
                        "published_at": datetime.now(timezone.utc).isoformat()})
        return summary
    finally:
        temp.unlink(missing_ok=True)


def _verify(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("PUBLISHED_DATABASE_INTEGRITY_CHECK_FAILED")
        actual = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [name for name in REQUIRED_TABLES if name not in actual]
        if missing:
            raise RuntimeError("PUBLISHED_DATABASE_REQUIRED_TABLES_MISSING:" + ",".join(missing))
        counts = {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in REQUIRED_TABLES}
    finally:
        conn.close()
    return {"integrity_check": integrity, "table_counts": counts}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
