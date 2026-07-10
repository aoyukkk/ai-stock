from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from database.session import get_engine


MIGRATIONS = {
    "llm_usage": {
        "prompt_version": "VARCHAR(64)",
        "model_alias": "VARCHAR(128)",
        "task_type": "VARCHAR(128)",
        "task_tier": "VARCHAR(32)",
        "thinking_mode": "VARCHAR(32)",
        "reasoning_effort": "VARCHAR(32)",
        "input_cache_hit_tokens": "INTEGER DEFAULT 0",
        "input_cache_miss_tokens": "INTEGER DEFAULT 0",
        "cost_status": "VARCHAR(64)",
        "pricing_version": "VARCHAR(128)",
    },
    "ai_analysis_result": {
        "model_alias": "VARCHAR(128)",
        "task_tier": "VARCHAR(32)",
        "request_hash": "VARCHAR(128)",
        "is_real": "BOOLEAN NOT NULL DEFAULT FALSE",
        "structured_result_json": "JSON",
    },
}


def migrate() -> dict[str, list[str]]:
    engine = get_engine()
    inspector = inspect(engine)
    applied: dict[str, list[str]] = {}
    with engine.begin() as connection:
        for table, columns in MIGRATIONS.items():
            existing = {item["name"] for item in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column in existing:
                    continue
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                applied.setdefault(table, []).append(column)
    return applied


def main() -> int:
    applied = migrate()
    print({"status": "ok", "applied_column_count": sum(map(len, applied.values()))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
