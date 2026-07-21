from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.performance import SelectionCohort, SelectionPerformanceRun
from database.session import get_session, init_db
from review.performance_schemas import PerformanceRequest
from review.selection_performance_service import SelectionPerformanceService
from scripts.build_selection_review_comparison import export_review_workbook


REPORTS = (
    ("FINAL_CANDIDATES", "今日推荐复盘"),
    ("KEY_CANDIDATES", "重点候选复盘"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="为历史交易日补齐今日推荐与重点候选两套复盘表。")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--force-recalculate", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    init_db()
    session = get_session()
    try:
        trade_dates = _trade_dates(session, args.start_date, args.end_date)
        outputs = []
        for evaluation_date in trade_dates:
            for scope, label in REPORTS:
                run = _run_performance(
                    session,
                    evaluation_date,
                    scope,
                    force_recalculate=args.force_recalculate,
                )
                output = (
                    ROOT
                    / "outputs"
                    / evaluation_date.isoformat()
                    / "复盘"
                    / f"{label}_截至{evaluation_date.isoformat()}.xlsx"
                )
                result = export_review_workbook(session, run, output, include_midday=True)
                outputs.append({
                    "trade_date": evaluation_date.isoformat(),
                    "scope": scope,
                    "performance_run_id": run.run_id,
                    "stock_count": run.stock_count,
                    "output": str(output),
                    "size_bytes": result["size_bytes"],
                    "sheets": result["sheets"],
                })
        print(json.dumps({
            "dates": [value.isoformat() for value in trade_dates],
            "workbook_count": len(outputs),
            "outputs": outputs,
            "llm_calls": 0,
            "per_stock_api_calls": 0,
        }, ensure_ascii=False, indent=2))
    finally:
        session.close()
    return 0


def _trade_dates(session, start_date: date | None, end_date: date | None) -> list[date]:
    query = select(SelectionCohort.selection_trade_date).distinct()
    if start_date:
        query = query.where(SelectionCohort.selection_trade_date >= start_date)
    if end_date:
        query = query.where(SelectionCohort.selection_trade_date <= end_date)
    return list(session.scalars(query.order_by(SelectionCohort.selection_trade_date)))


def _run_performance(
    session,
    evaluation_date: date,
    selection_scope: str,
    *,
    force_recalculate: bool,
) -> SelectionPerformanceRun:
    service = SelectionPerformanceService(session)
    request = PerformanceRequest(
        evaluation_end_date=evaluation_date,
        lookback_value=7,
        lookback_unit="CUSTOM",
        start_selection_date=evaluation_date - timedelta(days=6),
        end_selection_date=evaluation_date,
        return_basis="SIGNAL_CLOSE",
        selection_scope=selection_scope,
        weighting_mode="EQUAL_WEIGHT",
        include_zero_position_stocks=True,
        include_risk_blocked_stocks=True,
        force_recalculate=force_recalculate,
    )
    started = service.start(request)
    if started.get("job_id"):
        service.execute(started["performance_run_id"], started["job_id"])
    run = session.scalar(
        select(SelectionPerformanceRun).where(
            SelectionPerformanceRun.run_id == started["performance_run_id"]
        )
    )
    if run is None or run.status not in {"SUCCESS", "PARTIAL_SUCCESS"}:
        status = run.status if run else "NOT_FOUND"
        raise RuntimeError(f"SELECTION_PERFORMANCE_BACKFILL_FAILED:{evaluation_date}:{selection_scope}:{status}")
    return run


if __name__ == "__main__":
    raise SystemExit(main())
