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
from services.ranking_evaluation.snapshot_service import RankingSnapshotService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze a pre-LLM Quant Top100 snapshot.")
    parser.add_argument("--trade-date", required=True, type=date.fromisoformat)
    parser.add_argument("--source-quant-run-id", required=True)
    parser.add_argument("--factor-version", required=True)
    parser.add_argument("--allow-historical-import", action="store_true")
    args = parser.parse_args()
    init_db()
    session = get_session()
    try:
        result = RankingSnapshotService(session).capture(
            trade_date=args.trade_date,
            source_quant_run_id=args.source_quant_run_id,
            factor_version=args.factor_version,
            allow_historical_import=args.allow_historical_import,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
