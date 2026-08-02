from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db  # noqa: E402
from database.models.full_universe_effectiveness import FullUniverseQuantSnapshot  # noqa: E402
from services.ranking_evaluation.full_universe_service import (  # noqa: E402
    FullUniverseQuantEffectivenessService,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of-date", type=date.fromisoformat, required=True)
    parser.add_argument("--factor-version", required=True)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--no-network", action="store_true", default=True)
    parser.add_argument("--skip-excel", action="store_true")
    args = parser.parse_args()
    init_db()
    session = get_session()
    try:
        dates = list(
            session.scalars(
                select(FullUniverseQuantSnapshot.ranking_trade_date)
                .where(
                    FullUniverseQuantSnapshot.quant_factor_version
                    == args.factor_version
                )
                .order_by(FullUniverseQuantSnapshot.ranking_trade_date)
            )
        )
        if not dates:
            raise ValueError("FULL_UNIVERSE_SNAPSHOT_MISSING")
        result = FullUniverseQuantEffectivenessService(session).evaluate(
            start_date=args.start_date or dates[0],
            end_date=args.end_date or dates[-1],
            as_of_date=args.as_of_date,
            factor_version=args.factor_version,
            skip_excel=True,
            export_only=False,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
