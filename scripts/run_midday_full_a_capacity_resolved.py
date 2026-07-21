from __future__ import annotations

import argparse
import json
import sys
from datetime import date, time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.runtime_paths import output_root
from database.session import get_session, init_db
from midday.capacity import CapacityResolvedFullARunner
from scripts.run_daily_routine import _assert_advisory_only


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve iFinD capacity before the full-A V2.2 midday radar")
    parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    parser.add_argument("--cutoff-time", type=time.fromisoformat, required=True)
    parser.add_argument("--capacity-mode", choices=["ADAPTIVE_COVERAGE"], default="ADAPTIVE_COVERAGE")
    parser.add_argument("--max-run-scoped-provider-calls", type=int, choices=[400], default=400)
    parser.add_argument("--real-provider", action="store_true")
    parser.add_argument("--real-llm", action="store_true")
    parser.add_argument("--no-orders", action="store_true")
    parser.add_argument("--no-scheduler", action="store_true")
    parser.add_argument("--no-production-change", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    _assert_advisory_only()
    if not all((args.real_provider, args.real_llm, args.no_orders, args.no_scheduler, args.no_production_change)):
        raise SystemExit("FULL_A_EXPLICIT_REAL_SHADOW_FLAGS_REQUIRED")
    init_db()
    session = get_session()
    try:
        result = CapacityResolvedFullARunner(session, output_root=output_root()).run(
            args.trade_date,
            args.cutoff_time,
            max_run_scoped_provider_calls=args.max_run_scoped_provider_calls,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") in {
            "FULL_A_MIDDAY_SUCCESS",
            "FULL_A_MIDDAY_EMPTY_BUY_READY",
            "FULL_A_RULE_ONLY_INCOMPLETE",
            "FULL_A_PARTIAL_SUCCESS",
        } else 2
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
