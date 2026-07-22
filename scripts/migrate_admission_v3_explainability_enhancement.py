from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_engine, init_db


def main() -> None:
    engine = get_engine()
    init_db(engine)
    columns = {item["name"] for item in inspect(engine).get_columns("admission_v3_result")}
    additions = {
        "expected_value_score": "NUMERIC(14,6)",
        "risk_adjusted_opportunity_score": "NUMERIC(14,6)",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE admission_v3_result ADD COLUMN {name} {sql_type}"))
                print(f"added admission_v3_result.{name}")
            else:
                print(f"exists admission_v3_result.{name}")
    print("factor_performance_history ready")


if __name__ == "__main__":
    main()
