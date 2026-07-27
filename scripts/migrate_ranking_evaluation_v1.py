from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import DEFAULT_SQLITE_PATH  # noqa: E402


MIGRATION = ROOT / "database" / "migrations" / "20260727_ranking_forward_effectiveness_v1.sql"


def main() -> int:
    database = DEFAULT_SQLITE_PATH
    sql = MIGRATION.read_text(encoding="utf-8")
    with sqlite3.connect(database) as connection:
        connection.executescript(sql)
        connection.commit()
    print(f"RANKING_EVALUATION_V1_MIGRATION_APPLIED:{database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
