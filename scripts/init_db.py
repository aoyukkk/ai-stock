from __future__ import annotations

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import inspect

from database.init_db import init_database
from database.session import get_database_type, get_database_url, get_engine


def main() -> int:
    database_url = get_database_url()
    engine = get_engine(database_url)
    init_database(engine)
    table_count = len(inspect(engine).get_table_names())
    engine.dispose()

    print(
        "Database initialized: "
        f"type={get_database_type(database_url)}, tables={table_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
