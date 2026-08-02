from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db
from quant.explainability.service import DecisionExplainabilityShadowService


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Decision Explainability + Admission V3 in shadow mode.")
    parser.add_argument("--trade-date", required=True, type=date.fromisoformat)
    parser.add_argument("--source-v2-run-id")
    parser.add_argument("--confirm-shadow", action="store_true")
    args = parser.parse_args()
    init_db()
    session = get_session()
    try:
        result = DecisionExplainabilityShadowService(session).run(
            args.trade_date,
            source_v2_run_id=args.source_v2_run_id,
            force_shadow=args.confirm_shadow,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
