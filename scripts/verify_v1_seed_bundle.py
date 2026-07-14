from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from dotenv import dotenv_values

from scripts.v1_release_common import scan_text, sha256_file


ROOT = ROOT_BOOTSTRAP


def verify_seed_bundle(seed_root: Path, *, known_secrets: Iterable[str] | None = None, update_manifest: bool = False) -> dict[str, Any]:
    seed_root = seed_root.resolve()
    manifest_path = seed_root / "SEED_DATA_MANIFEST_1.0.0.json"
    database = seed_root / "data" / "ai_trader_seed.db"
    problems: list[str] = []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if not manifest: problems.append("MANIFEST_MISSING")
    if not database.is_file(): problems.append("DATABASE_MISSING")
    secrets = list(known_secrets or _known_secrets())

    if database.is_file():
        if manifest.get("sanitized_database_sha256") != sha256_file(database): problems.append("DATABASE_CHECKSUM_MISMATCH")
        with sqlite3.connect(database) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok": problems.append("DATABASE_INTEGRITY_FAILED")
            if list(connection.execute("PRAGMA foreign_key_check")): problems.append("DATABASE_FOREIGN_KEY_FAILED")
            dump = "\n".join(connection.iterdump())
            scan = scan_text(dump, secrets)
            if scan["secret_hits"]: problems.append(f"DATABASE_SECRET_HITS:{scan['secret_hits']}")
            if scan["absolute_path_hits"]: problems.append(f"DATABASE_ABSOLUTE_PATH_HITS:{scan['absolute_path_hits']}")
            dates = {str(row[0]) for row in connection.execute("SELECT trade_date FROM workbench_run_registry")}
            if "2026-07-10" not in dates: problems.append("REQUIRED_HISTORY_DATE_MISSING")
            if int(connection.execute("SELECT COUNT(*) FROM trade_order").fetchone()[0]): problems.append("TRADING_ROWS_PRESENT")
            if int(connection.execute("SELECT COUNT(*) FROM pipeline_job WHERE stage='MOCK_COMPLETED'").fetchone()[0]): problems.append("MOCK_JOB_PRESENT")

    for item in manifest.get("included_files", []):
        path = seed_root / item["destination_path"]
        if not path.is_file(): problems.append(f"FILE_MISSING:{item['destination_path']}"); continue
        if path.stat().st_size != item["file_size"] or sha256_file(path) != item["sha256"]:
            problems.append(f"FILE_CHECKSUM_MISMATCH:{item['destination_path']}")
        if path.suffix.lower() in {".json", ".txt", ".md"}:
            scan = scan_text(path.read_text(encoding="utf-8", errors="replace"), secrets)
            if scan["secret_hits"]: problems.append(f"FILE_SECRET_HITS:{item['destination_path']}")
            if scan["absolute_path_hits"]: problems.append(f"FILE_ABSOLUTE_PATH_HITS:{item['destination_path']}")

    return {"passed": not problems, "problems": problems, "file_count": len(manifest.get("included_files", []))}


def _known_secrets() -> list[str]:
    values = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}
    names = ("TUSHARE_TOKEN", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN")
    return [str(os.getenv(name) or values.get(name) or "").strip() for name in names if os.getenv(name) or values.get(name)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=ROOT / "build" / "seed")
    args = parser.parse_args()
    result = verify_seed_bundle(args.seed)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
