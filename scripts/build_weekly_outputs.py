from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db
from database.models.performance import SelectionPerformanceRun
from database.models.stock import StockMaster
from market_review.service import MarketReviewService
from market_review.weekly_excel import WeeklyMarketReviewExcelExporter
from review.performance_schemas import PerformanceRequest
from review.selection_performance_service import SelectionPerformanceService
from scripts.build_selection_review_comparison import export_weekly_stock_comparison
from stock_codes import normalize_ts_code


def main() -> int:
    parser = argparse.ArgumentParser(description="生成本周大盘复盘和今日推荐中的模型选股周对比。")
    parser.add_argument("--week-start", required=True, type=date.fromisoformat)
    parser.add_argument("--week-end", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    if args.week_start > args.week_end:
        raise ValueError("WEEK_START_AFTER_END")

    load_dotenv(ROOT / ".env", override=False)
    init_db()
    output_dir = ROOT / "outputs" / args.week_end.isoformat() / "复盘"
    session = get_session()
    try:
        market_service = MarketReviewService(session)
        bundles = []
        current = args.week_start
        while current <= args.week_end:
            bundle = market_service.latest(current)
            if bundle is not None:
                bundles.append(bundle)
            current = date.fromordinal(current.toordinal() + 1)
        if not bundles:
            raise ValueError("WEEKLY_MARKET_REVIEW_RUNS_NOT_FOUND")
        market_output = output_dir / f"本周大盘复盘_{args.week_start.isoformat()}_至_{args.week_end.isoformat()}.xlsx"
        flow_data = _load_industry_moneyflow(session, [
            date.fromisoformat(str((bundle.get("run") or {})["trade_date"])) for bundle in bundles
        ])
        market_result = WeeklyMarketReviewExcelExporter().export(
            market_output, bundles, industry_moneyflow=flow_data,
        )

        performance = SelectionPerformanceService(session)
        model_request = PerformanceRequest(
            evaluation_end_date=args.week_end,
            lookback_value=5,
            lookback_unit="CUSTOM",
            start_selection_date=args.week_start,
            end_selection_date=args.week_end,
            return_basis="SIGNAL_CLOSE",
            selection_scope="FINAL_LLM_ONLY",
            weighting_mode="EQUAL_WEIGHT",
            include_zero_position_stocks=True,
            include_risk_blocked_stocks=True,
        )
        comparison_output = output_dir / f"本周今日推荐_模型选股对比_{args.week_start.isoformat()}_至_{args.week_end.isoformat()}.xlsx"
        model_comparison = _build_weekly_comparison(
            session, performance, model_request, comparison_output,
        )

        key_request = PerformanceRequest(
            evaluation_end_date=args.week_end,
            lookback_value=5,
            lookback_unit="CUSTOM",
            start_selection_date=args.week_start,
            end_selection_date=args.week_end,
            return_basis="SIGNAL_CLOSE",
            selection_scope="KEY_CANDIDATES",
            weighting_mode="EQUAL_WEIGHT",
            include_zero_position_stocks=True,
            include_risk_blocked_stocks=True,
        )
        key_output = output_dir / f"本周重点候选对比_{args.week_start.isoformat()}_至_{args.week_end.isoformat()}.xlsx"
        key_comparison = _build_weekly_comparison(
            session, performance, key_request, key_output,
        )
        result = {
            "week_start": args.week_start.isoformat(),
            "week_end": args.week_end.isoformat(),
            "market_review": market_result,
            "model_only_comparison": model_comparison,
            "key_candidates_comparison": key_comparison,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        session.close()
    return 0


def _build_weekly_comparison(
    session,
    performance: SelectionPerformanceService,
    request: PerformanceRequest,
    output: Path,
) -> dict:
    started = performance.start(request)
    run_id = str(started.get("performance_run_id") or started.get("existing_run_id"))
    if started.get("job_id"):
        performance.execute(run_id, str(started["job_id"]))
    run = session.scalar(select(SelectionPerformanceRun).where(SelectionPerformanceRun.run_id == run_id))
    if run is None:
        raise ValueError("WEEKLY_PERFORMANCE_RUN_NOT_FOUND")
    exported = export_weekly_stock_comparison(session, run, output)
    detail = performance.run_detail(run_id)
    return {
        **exported,
        "performance_run_id": run_id,
        "selection_scope": detail["selection_scope"],
        "cohort_count": detail["cohort_count"],
        "source_record_count": detail["stock_count"],
        "stock_count": exported.get("displayed_stock_count", detail["stock_count"]),
        "coverage_ratio": detail["coverage_ratio"],
        "llm_calls": 0,
    }


def _load_industry_moneyflow(session, trade_days: list[date]) -> dict[date, dict]:
    industry_by_code = {
        normalize_ts_code(code): industry
        for code, industry in session.execute(select(StockMaster.code, StockMaster.industry))
        if industry
    }
    result: dict[date, dict] = {}
    for trade_day in trade_days:
        path = ROOT / "data" / "cache" / "tushare" / "trade_date" / "moneyflow" / f"{trade_day:%Y%m%d}.json"
        if not path.exists():
            result[trade_day] = {"values": {}, "coverage": 0.0, "source": "NOT_AVAILABLE"}
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        values: dict[str, float] = defaultdict(float)
        matched = 0
        valid = 0
        for row in rows if isinstance(rows, list) else []:
            raw = row.get("net_mf_amount")
            if raw is None:
                continue
            valid += 1
            industry = industry_by_code.get(normalize_ts_code(str(row.get("ts_code") or "")))
            if not industry:
                continue
            matched += 1
            # Tushare moneyflow amount fields are denominated in ten-thousand yuan.
            values[str(industry)] += float(raw) / 10_000
        result[trade_day] = {
            "values": dict(values),
            "coverage": matched / valid if valid else 0.0,
            "source": "TUSHARE_MONEYFLOW_TRADE_DATE_CACHE",
        }
    return result


if __name__ == "__main__":
    raise SystemExit(main())
