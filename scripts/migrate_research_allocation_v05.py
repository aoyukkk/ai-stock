from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.session import get_engine, init_db


TABLES = (
    "fundamental_research_run",
    "research_evidence",
    "stock_fundamental_profile",
    "allocation_run",
    "position_suggestion",
    "pending_verification_task",
)

COLUMNS = {
    "fundamental_research_run": {
        "request_hash": "VARCHAR(64)",
        "config_snapshot": "JSON NOT NULL DEFAULT '{}'",
        "is_current": "BOOLEAN NOT NULL DEFAULT TRUE",
    },
    "stock_fundamental_profile": {
        "field_provenance_map": "JSON NOT NULL DEFAULT '{}'",
        "available_at": "DATETIME",
        "request_hash": "VARCHAR(64)",
        "is_current": "BOOLEAN NOT NULL DEFAULT TRUE",
    },
    "allocation_run": {
        "request_hash": "VARCHAR(64)",
        "is_current": "BOOLEAN NOT NULL DEFAULT TRUE",
    },
}


def migrate() -> dict[str, list[str]]:
    engine = get_engine()
    before = set(inspect(engine).get_table_names())
    init_db(engine)
    inspector = inspect(engine)
    after = set(inspector.get_table_names())
    altered = []
    with engine.begin() as connection:
        for table, columns in COLUMNS.items():
            existing = {item["name"] for item in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                    altered.append(f"{table}.{column}")
    return {"created": sorted((after - before).intersection(TABLES)), "altered": altered}


if __name__ == "__main__":
    result = migrate()
    print({"status": "ok", **result})
