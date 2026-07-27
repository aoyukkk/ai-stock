from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db  # noqa: E402
from services.ranking_evaluation.outcome_backfill_service import OutcomeBackfillService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Incrementally mature ranking outcomes.")
    parser.add_argument("--as-of-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--factor-version")
    args = parser.parse_args()
    init_db()
    session = get_session()
    try:
        result = OutcomeBackfillService(session).refresh(
            as_of_date=args.as_of_date,
            factor_version=args.factor_version,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
