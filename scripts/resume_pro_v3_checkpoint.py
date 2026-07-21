from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.application.pro_v3 import ProductionProV3ApplicationService
from backend.core.runtime_paths import output_root
from database.session import get_session, init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume Pro V3 from an existing Flash checkpoint.")
    parser.add_argument("--flash-run-id", required=True)
    parser.add_argument("--account-equity", type=Decimal, default=Decimal("1000000"))
    parser.add_argument("--available-cash", type=Decimal, default=Decimal("1000000"))
    args = parser.parse_args()

    load_dotenv(ROOT_DIR / ".env", override=False)
    init_db()
    session = get_session()
    try:
        result = ProductionProV3ApplicationService(session, output_root()).run(
            args.flash_run_id,
            account_equity=args.account_equity,
            available_cash=args.available_cash,
        )
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
