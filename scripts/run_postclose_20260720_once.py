from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from database.session import init_db
from post_close.official_run import OFFICIAL_SUCCESS, PostCloseOfficialRunner
from reporting.workbook_style import WorkbookStyleService


def _reference_workbook() -> Path:
    return WorkbookStyleService.resolve_recent_successful_reference(
        ROOT / "outputs",
        start=date(2026, 7, 13),
        end=date(2026, 7, 17),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the one-time 2026-07-20 post-close Full-A workflow.")
    parser.add_argument("--trade-date", default="2026-07-20")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dry-run-fixture-date", default="2026-07-17")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.trade_date)
    reference = _reference_workbook()
    init_db()
    if args.dry_run:
        fixture_date = date.fromisoformat(args.dry_run_fixture_date)
        result = PostCloseOfficialRunner(ROOT, fixture_date, reference).dry_run()
        result["scheduled_trade_date"] = target_date.isoformat()
    else:
        result = PostCloseOfficialRunner(ROOT, target_date, reference).run()

    print(json.dumps(result, ensure_ascii=True, default=str))
    if args.dry_run:
        return 0 if result.get("status") == "DRY_RUN_READY" else 2
    return 0 if result.get("final_status") in OFFICIAL_SUCCESS else 2


if __name__ == "__main__":
    raise SystemExit(main())
