from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts._common import ROOT_DIR


LOCAL_SQLITE_URL = "sqlite:///./data/ai_trader_dev.db"


def initialize_local_environment(root: Path = ROOT_DIR) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    env_path = root / ".env"
    env_example = root / ".env.example"
    if not env_path.exists():
        shutil.copyfile(env_example, env_path)
        print("Created .env from .env.example")
    else:
        print(".env already exists")

    import database.models  # noqa: F401
    from database.init_db import init_database
    from database.session import create_engine_from_url

    engine = create_engine_from_url(LOCAL_SQLITE_URL)
    init_database(engine)
    engine.dispose()
    print("Initialized local SQLite database at ./data/ai_trader_dev.db")
    print("Safety mode: ENABLE_REAL_TRADING=false, data_source=mock, llm=mock")


def main() -> int:
    initialize_local_environment()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
