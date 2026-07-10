from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.session import get_engine, init_db


ALLOCATION_COLUMNS = {
    "quant_run_id": "VARCHAR(64)",
    "run_data_manifest_id": "VARCHAR(64)",
    "order_plan_id": "INTEGER",
    "account_snapshot_time": "VARCHAR(64)",
}


def migrate() -> dict:
    engine = get_engine(); before = set(inspect(engine).get_table_names())
    init_db(engine); inspector = inspect(engine); after = set(inspector.get_table_names())
    existing = {item["name"] for item in inspector.get_columns("allocation_run")}
    altered = []
    with engine.begin() as connection:
        for column, sql_type in ALLOCATION_COLUMNS.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE allocation_run ADD COLUMN {column} {sql_type}"))
                altered.append(f"allocation_run.{column}")
    return {"created": sorted(after - before), "altered": altered}


if __name__ == "__main__":
    print({"status": "ok", **migrate()})
