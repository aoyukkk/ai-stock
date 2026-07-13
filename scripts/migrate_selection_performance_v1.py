from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.session import get_engine, init_db


ADDITIVE_COLUMNS = {
    "selection_performance_daily": {
        "baseline_trade_date": "DATE",
        "baseline_price": "NUMERIC(16, 6)",
    },
}


def migrate() -> dict[str, list[str]]:
    engine = get_engine()
    init_db(engine)
    inspector = inspect(engine)
    changed: dict[str, list[str]] = {}
    with engine.begin() as connection:
        for table, columns in ADDITIVE_COLUMNS.items():
            if table not in inspector.get_table_names():
                continue
            existing = {item["name"] for item in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name in existing:
                    continue
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                changed.setdefault(table, []).append(name)
    return changed


if __name__ == "__main__":
    result = migrate()
    print({"status": "PASS", "changed": result})
