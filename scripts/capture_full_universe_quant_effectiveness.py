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
    parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    parser.add_argument("--quant-run-id", required=True)
    parser.add_argument("--factor-version", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-network", action="store_true", default=True)
    parser.add_argument("--scope", default="FULL_SCORED_UNIVERSE")
    parser.add_argument("--return-basis", default="RAW_CLOSE_SIGNAL_RETURN")
    args = parser.parse_args()
    if args.scope != "FULL_SCORED_UNIVERSE":
        raise ValueError("UNSUPPORTED_CAPTURE_SCOPE")
    if args.return_basis != "RAW_CLOSE_SIGNAL_RETURN":
        raise ValueError("RETURN_BASIS_MISMATCH")
    init_db()
    session = get_session()
    try:
        result = FullUniverseQuantEffectivenessService(session).capture(
            trade_date=args.trade_date,
            quant_run_id=args.quant_run_id,
            factor_version=args.factor_version,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
