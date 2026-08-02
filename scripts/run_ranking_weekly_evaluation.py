from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db  # noqa: E402
from services.ranking_evaluation.weekly_report_service import RankingWeeklyReportService  # noqa: E402


def _previous_friday(value: date) -> date:
    return value - timedelta(days=(value.weekday() - 4) % 7)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an immutable Friday cumulative report.")
    parser.add_argument("--week-ending", type=date.fromisoformat)
    parser.add_argument("--factor-version", required=True)
    args = parser.parse_args()
    week_ending = args.week_ending or _previous_friday(date.today())
    init_db()
    session = get_session()
    try:
        result = RankingWeeklyReportService(session).run(
            week_ending=week_ending,
            factor_version=args.factor_version,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
