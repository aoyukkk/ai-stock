from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.quant_run import QuantRun  # noqa: E402
from database.session import get_session, init_db  # noqa: E402
from services.ranking_evaluation.outcome_backfill_service import OutcomeBackfillService  # noqa: E402
from services.ranking_evaluation.snapshot_service import RankingSnapshotService  # noqa: E402
from services.ranking_evaluation.weekly_report_service import RankingWeeklyReportService  # noqa: E402


def _previous_friday(value: date) -> date:
    return value - timedelta(days=(value.weekday() - 4) % 7)


def _canonical_factor(run: QuantRun) -> str | None:
    source = str(run.factor_version or "")
    if "TUSHARE_QUANT_V2_CORRECTED_SHADOW" in source.upper():
        return "TUSHARE_QUANT_V2_CORRECTED_SHADOW"
    if bool(run.actionable):
        return "TUSHARE_BASELINE_V1"
    return None


def _daily(session, as_of_date: date) -> dict:
    runs = list(
        session.scalars(
            select(QuantRun)
            .where(
                QuantRun.base_market_trade_date == as_of_date,
                QuantRun.status == "COMPLETED",
                QuantRun.no_llm_call_verified.is_(True),
            )
            .order_by(QuantRun.created_at.desc(), QuantRun.id.desc())
        )
    )
    latest: dict[str, QuantRun] = {}
    for run in runs:
        factor = _canonical_factor(run)
        if factor and factor not in latest:
            latest[factor] = run
    captures = []
    service = RankingSnapshotService(session)
    for factor, run in latest.items():
        captures.append(
            service.capture(
                trade_date=as_of_date,
                source_quant_run_id=run.run_id,
                factor_version=factor,
            )
        )
    outcomes = OutcomeBackfillService(session).refresh(as_of_date=as_of_date)
    return {
        "mode": "daily",
        "as_of_date": as_of_date,
        "captures": captures,
        "outcomes": outcomes,
        "shadow_only": True,
        "llm_calls": 0,
        "orders": 0,
        "scheduler": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="One-shot ranking evaluation entrypoint.")
    parser.add_argument("--mode", required=True, choices=("daily", "weekly"))
    parser.add_argument("--as-of-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--week-ending", type=date.fromisoformat)
    parser.add_argument("--factor-version")
    args = parser.parse_args()
    init_db()
    session = get_session()
    try:
        if args.mode == "daily":
            result = _daily(session, args.as_of_date)
        else:
            if not args.factor_version:
                parser.error("--factor-version is required for weekly mode")
            week_ending = args.week_ending or _previous_friday(args.as_of_date)
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
