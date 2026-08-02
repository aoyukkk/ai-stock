from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_engine, init_db


FACTOR_COLUMNS = {
    "positive_contribution_count": "INTEGER", "negative_contribution_count": "INTEGER",
    "mean_contribution": "NUMERIC(14,6)", "median_contribution": "NUMERIC(14,6)",
    "contribution_return_correlation": "NUMERIC(14,6)", "contribution_rank_ic": "NUMERIC(14,6)",
    "top_contribution_return": "NUMERIC(14,6)", "bottom_contribution_return": "NUMERIC(14,6)",
    "contribution_hit_rate": "NUMERIC(14,6)", "average_mfe": "NUMERIC(14,6)",
    "profit_factor": "NUMERIC(14,6)", "pass_contribution_json": "JSON", "rank_contribution_json": "JSON",
}


def main() -> None:
    engine = get_engine()
    init_db(engine)
    columns = {item["name"] for item in inspect(engine).get_columns("factor_performance_history")}
    with engine.begin() as connection:
        for name, sql_type in FACTOR_COLUMNS.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE factor_performance_history ADD COLUMN {name} {sql_type}"))
                print(f"added factor_performance_history.{name}")
    print("forward shadow evaluation tables ready")


if __name__ == "__main__":
    main()
