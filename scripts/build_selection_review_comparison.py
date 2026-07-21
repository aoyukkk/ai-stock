from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.midday import MiddayRecommendationResult, MiddayRecommendationRun
from database.models.performance import SelectionCohort, SelectionPerformanceRun
from database.session import get_session
from backend.core.config_manager import ConfigManager
from midday.human_excel import ACTION_LABELS
from review.human_performance_excel import HumanPerformanceExcelExporter, WeeklyStockPerformanceExcelExporter
from review.performance_cohorts import SelectionCohortResolver
from review.performance_market import MarketDataBatchLoader
from review.selection_performance_service import SelectionPerformanceService
from stock_codes import display_stock_code


def main() -> int:
    parser = argparse.ArgumentParser(description="导出最近推荐股票的逐日涨跌、累计涨跌和午盘监测。")
    parser.add_argument("--performance-run-id")
    parser.add_argument("--evaluation-end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report-kind", choices=("today-recommendation", "key-candidates"))
    parser.add_argument("--no-midday", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    session = get_session()
    try:
        run = _select_run(session, args.performance_run_id, args.evaluation_end_date)
        expected_scope = {"today-recommendation": "FINAL_CANDIDATES", "key-candidates": "KEY_CANDIDATES"}.get(args.report_kind)
        if expected_scope and run.selection_scope != expected_scope:
            raise ValueError(f"PERFORMANCE_SCOPE_MISMATCH:{run.selection_scope}:{expected_scope}")
        output = args.output or _default_output(run.selection_scope, args.evaluation_end_date)
        result = export_review_workbook(
            session,
            run,
            output.resolve(),
            include_midday=not args.no_midday,
        )
        result.update({
            "performance_run_id": run.run_id,
            "evaluation_end_date": run.evaluation_end_date.isoformat(),
            "cohort_count": run.cohort_count,
            "stock_count": run.stock_count,
            "daily_record_count": run.daily_record_count,
            "coverage_ratio": float(run.coverage_ratio),
            "midday_count": result.pop("midday_count", 0),
            "llm_calls": 0,
            "per_stock_api_calls": 0,
        })
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        session.close()
    return 0


def export_review_workbook(
    session,
    run: SelectionPerformanceRun,
    output: Path,
    *,
    include_midday: bool = True,
) -> dict:
    service = SelectionPerformanceService(session)
    payload = {
        "run": service.run_detail(run.run_id),
        "cohorts": service.cohort_summary(run.run_id),
        "daily": service.portfolio_daily(run.run_id),
        "stocks": service.stock_daily(run.run_id),
        "members": _member_rows(session, run),
    }
    if include_midday:
        payload["midday"] = _midday_payload(
            session,
            run.evaluation_end_date,
            selection_scope=run.selection_scope,
        )
    result = HumanPerformanceExcelExporter().export(output.resolve(), payload)
    result["midday_count"] = len((payload.get("midday") or {}).get("rows") or [])
    return result


def export_weekly_stock_comparison(session, run: SelectionPerformanceRun, output: Path) -> dict:
    """Export the weekly model recommendations as one vertically stacked table."""
    service = SelectionPerformanceService(session)
    payload = {
        "run": service.run_detail(run.run_id),
        "stocks": service.stock_daily(run.run_id),
        "members": _member_rows(session, run),
    }
    return WeeklyStockPerformanceExcelExporter().export(output.resolve(), payload)


def _select_run(session, run_id: str | None, evaluation_end_date: date) -> SelectionPerformanceRun:
    query = select(SelectionPerformanceRun).where(
        SelectionPerformanceRun.status.in_(["SUCCESS", "PARTIAL_SUCCESS"]),
        SelectionPerformanceRun.evaluation_end_date == evaluation_end_date,
    )
    if run_id:
        query = query.where(SelectionPerformanceRun.run_id == run_id)
    run = session.scalar(query.order_by(SelectionPerformanceRun.completed_at.desc()))
    if run is None:
        raise ValueError("SELECTION_PERFORMANCE_RUN_NOT_FOUND")
    return run


def _member_rows(session, run: SelectionPerformanceRun) -> list[dict]:
    pipeline_ids = set((run.config_snapshot_json or {}).get("input_components", {}).get("pipeline_run_ids", []))
    cohorts = list(session.scalars(select(SelectionCohort).where(SelectionCohort.pipeline_run_id.in_(pipeline_ids))))
    result = []
    resolver = SelectionCohortResolver(session)
    for cohort in cohorts:
        members = resolver.members(
            cohort,
            run.selection_scope,
            run.include_zero_position_stocks,
            run.include_risk_blocked_stocks,
        )
        for member in members:
            result.append({
                "selection_trade_date": cohort.selection_trade_date,
                "stock_code": display_stock_code(member.stock_code),
                "stock_name": member.stock_name_snapshot,
                "selection_source": member.selection_source,
                "quant_rank": member.quant_rank,
                "pro_rank": member.pro_rank,
                "pro_score": float(member.pro_score) if member.pro_score is not None else None,
            })
    return result


def _default_output(selection_scope: str, evaluation_end_date: date) -> Path:
    label = "重点候选复盘" if selection_scope == "KEY_CANDIDATES" else "今日推荐复盘"
    return ROOT / "outputs" / evaluation_end_date.isoformat() / "复盘" / f"{label}_截至{evaluation_end_date.isoformat()}.xlsx"


def _midday_payload(
    session,
    evaluation_end_date: date,
    *,
    selection_scope: str = "FINAL_CANDIDATES",
) -> dict:
    run = session.scalar(
        select(MiddayRecommendationRun)
        .where(
            MiddayRecommendationRun.session_trade_date == evaluation_end_date,
            MiddayRecommendationRun.status.in_(["SUCCESS", "PARTIAL_SUCCESS", "WAITING_AFTERNOON_RECHECK"]),
        )
        .order_by(MiddayRecommendationRun.id.desc())
    )
    if run is None:
        return {}
    minimum_score = float(
        ConfigManager(session=session)
        .get_effective_config()["values"]
        .get("selection_performance.minimum_recommendation_score", 60)
    )
    query = select(MiddayRecommendationResult).where(
        MiddayRecommendationResult.run_id == run.run_id,
        MiddayRecommendationResult.pro_rank.is_not(None),
    )
    if selection_scope != "KEY_CANDIDATES":
        query = query.where(MiddayRecommendationResult.midday_pro_score >= minimum_score)
    rows = list(session.scalars(query.order_by(MiddayRecommendationResult.pro_rank)))
    loader = MarketDataBatchLoader(session)
    bars, _ = loader.load({row.stock_code for row in rows}, [evaluation_end_date])
    return {
        "run_id": run.run_id,
        "decision_time": run.decision_time.isoformat(),
        "minimum_recommendation_score": minimum_score,
        "rows": [{
            "rank": row.pro_rank,
            "stock_code": display_stock_code(row.stock_code),
            "stock_name": row.stock_name or "",
            "action": ACTION_LABELS.get(row.candidate_action, row.candidate_action),
            "pro_score": float(row.midday_pro_score) if row.midday_pro_score is not None else None,
            "reference_price": float(row.recommended_price) if row.recommended_price is not None else None,
            "close_price": (
                bars.get(row.stock_code, {}).get(evaluation_end_date).close
                if bars.get(row.stock_code, {}).get(evaluation_end_date)
                else None
            ),
        } for row in rows],
    }


if __name__ == "__main__":
    raise SystemExit(main())
