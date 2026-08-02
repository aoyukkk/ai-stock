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
from services.ranking_evaluation.full_universe_service import (  # noqa: E402
    FullUniverseQuantEffectivenessService,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--as-of-date", type=date.fromisoformat, required=True)
    parser.add_argument("--factor-version", required=True)
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--no-network", action="store_true", default=True)
    parser.add_argument("--scope", default="FULL_SCORED_UNIVERSE")
    parser.add_argument("--return-basis", default="RAW_CLOSE_SIGNAL_RETURN")
    parser.add_argument("--skip-excel", action="store_true")
    args = parser.parse_args()
    if args.scope != "FULL_SCORED_UNIVERSE":
        raise ValueError("UNSUPPORTED_EVALUATION_SCOPE")
    if args.return_basis != "RAW_CLOSE_SIGNAL_RETURN":
        raise ValueError("RETURN_BASIS_MISMATCH")
    init_db()
    session = get_session()
    try:
        result = FullUniverseQuantEffectivenessService(session).evaluate(
            start_date=args.start_date,
            end_date=args.end_date,
            as_of_date=args.as_of_date,
            factor_version=args.factor_version,
            skip_excel=args.skip_excel,
            export_only=args.export_only,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
