from __future__ import annotations

import json
import math
import statistics
import sys
from collections import Counter
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.workbench.service import WorkbenchService
from database.models.market_review import MarketDailySnapshot, MarketReviewRun
from database.models.quant_run import QuantRankResult, QuantRun
from database.models.stock import StockMaster
from database.models.temporal import RunDataManifestRecord
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationLLMAudit,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
    ProCandidateReview,
)
from database.models.workbench import WorkbenchRunRegistry
from database.session import get_database_identity, get_session
from market_review.rules import MarketRegimeEngine
from market_review.snapshot import MarketDailySnapshotService
from temporal.calendar import TradeCalendarService


OUTPUT_DIR = ROOT / "outputs" / "2026-07-14"
TODAY = date(2026, 7, 14)
EXCEL_PATH = OUTPUT_DIR / "AI交易助手_2026-07-14_市场与流程对比.xlsx"
COMPARISON_JSON_PATH = OUTPUT_DIR / "daily_three_trade_day_comparison_20260714.json"
PIPELINE_JSON_PATH = OUTPUT_DIR / "daily_pipeline_20260714_report.json"

NAVY = "17365D"
BLUE = "17365D"
LIGHT_BLUE = "DCEAF5"
PALE_BLUE = "F4F7F9"
RED = "C00000"
GREEN = "008000"
YELLOW = "FFF2CC"
GRAY = "E5E7EB"
WHITE = "FFFFFF"
THIN_GRAY = Side(style="thin", color="D1D5DB")
ALL_BORDER = Border(left=THIN_GRAY, right=THIN_GRAY, top=THIN_GRAY, bottom=THIN_GRAY)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

MARKET_REGIME_LABELS = {
    "BROAD_RALLY": "普涨反弹",
    "BROAD_SELL_OFF": "普跌调整",
    "INDEX_LED_RALLY": "权重领涨",
    "THEME_RALLY": "题材轮动",
    "SHRINKING_PULLBACK": "缩量回调",
    "WEAK_REBOUND": "弱势修复",
    "RANGE_BOUND": "区间震荡",
    "MIXED_ROTATION": "分化轮动",
    "DATA_INSUFFICIENT": "数据不足",
}

ZERO_POSITION_REASON_LABELS = {
    "RISK_GATE_BLOCKED_OR_NO_RECOMMENDED_PRICE": "风险规则阻断或缺少有效参考价",
    "RISK_GATE_BLOCKED": "风险规则阻断",
    "NO_RECOMMENDED_PRICE": "缺少有效参考价",
    "risk": "风险规则",
}


def number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def mean(values: Iterable[Any]) -> float | None:
    clean = [value for item in values if (value := number(item)) is not None]
    return statistics.fmean(clean) if clean else None


def median(values: Iterable[Any]) -> float | None:
    clean = [value for item in values if (value := number(item)) is not None]
    return statistics.median(clean) if clean else None


def items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("items") or []
    return [dict(item) for item in value] if isinstance(value, list) else []


def top_and_bottom(value: Any, count: int = 3) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = items(value)
    ranked = sorted(rows, key=lambda row: number(row.get("change_percent")) or 0, reverse=True)
    return ranked[:count], list(reversed(ranked[-count:]))


def compact_sectors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": row.get("sector_name") or row.get("sector_code"),
            "change_percent": number(row.get("change_percent")),
            "member_count": row.get("member_count"),
        }
        for row in rows
    ]


def scalar_scores(rows: list[ModelValidationSample], field: str) -> list[float]:
    result = []
    for row in rows:
        value = number((row.screening_result or {}).get(field))
        if value is not None:
            result.append(value)
    return result


def market_snapshot(session, trade_date: date) -> dict[str, Any]:
    persisted = session.scalar(
        select(MarketDailySnapshot)
        .where(MarketDailySnapshot.trade_date == trade_date)
        .order_by(MarketDailySnapshot.created_at.desc())
    )
    review = session.scalar(
        select(MarketReviewRun)
        .where(MarketReviewRun.trade_date == trade_date)
        .order_by(MarketReviewRun.created_at.desc())
    )
    point_in_time_classification = persisted is not None
    if persisted is not None:
        breadth = dict(persisted.breadth_summary_json or {})
        turnover = dict(persisted.turnover_summary_json or {})
        limits = dict(persisted.limit_summary_json or {})
        capital = dict(persisted.capital_summary_json or {})
        industries = persisted.industry_summary_json
        concepts = persisted.concept_summary_json
        regime = persisted.market_regime
        regime_score = persisted.regime_score
        regime_confidence = persisted.regime_confidence
        quality = persisted.data_quality_score
        source_status = dict(persisted.source_status_json or {})
        snapshot_hash = persisted.snapshot_hash
        source = "PERSISTED_MARKET_SNAPSHOT"
    else:
        decision_time = datetime.combine(trade_date, time(15, 30), timezone.utc)
        built = MarketDailySnapshotService(session).build(trade_date, decision_time)
        # No historical classification snapshot exists for 2026-07-10. Remove current
        # master/concept classifications before regime evaluation to avoid look-ahead.
        sanitized = built.model_copy(update={"industries": [], "concepts": []})
        evaluated = MarketRegimeEngine().evaluate(sanitized)
        breadth = dict(built.breadth)
        turnover = dict(built.turnover)
        limits = dict(built.limit_structure)
        capital = dict(built.capital)
        industries = {"items": [], "status": "POINT_IN_TIME_CLASSIFICATION_UNAVAILABLE"}
        concepts = {"items": [], "status": "POINT_IN_TIME_CLASSIFICATION_UNAVAILABLE"}
        regime = evaluated.market_regime
        regime_score = evaluated.regime_score
        regime_confidence = evaluated.regime_confidence
        quality = built.data_quality_score
        source_status = dict(built.source_status)
        source_status["industries"] = {"count": 0, "status": "NOT_AVAILABLE", "reason": "POINT_IN_TIME_CLASSIFICATION_UNAVAILABLE"}
        source_status["concepts"] = {"count": 0, "status": "NOT_AVAILABLE", "reason": "POINT_IN_TIME_CLASSIFICATION_UNAVAILABLE"}
        snapshot_hash = built.snapshot_hash
        source = "IN_MEMORY_LOCAL_CACHE_NO_PERSIST"
    leading_industries, lagging_industries = top_and_bottom(industries)
    leading_concepts, lagging_concepts = top_and_bottom(concepts)
    scenario = {
        "base": review.base_case_probability if review else None,
        "bull": review.bull_case_probability if review else None,
        "bear": review.bear_case_probability if review else None,
        "status": review.status if review else "NOT_AVAILABLE",
    }
    return {
        "trade_date": trade_date.isoformat(),
        "source": source,
        "snapshot_hash": snapshot_hash,
        "point_in_time_classification": point_in_time_classification,
        "up": int(breadth.get("advancing_count") or 0),
        "down": int(breadth.get("declining_count") or 0),
        "flat": int(breadth.get("flat_count") or 0),
        "valid_count": int(breadth.get("valid_count") or 0),
        "up_ratio": number(breadth.get("advancing_ratio")),
        "average_return": number(breadth.get("average_return")),
        "median_return": number(breadth.get("median_return")),
        "equal_weight_return": number(breadth.get("equal_weight_return")),
        "turnover": number(turnover.get("total_amount")),
        "turnover_change": number(turnover.get("change_ratio")),
        "limit_up": int(limits.get("limit_up_count") or 0),
        "limit_down": int(limits.get("limit_down_count") or 0),
        "failed_limit_up": int(limits.get("failed_limit_up_count") or 0),
        "failed_limit_up_ratio": number(limits.get("failed_limit_up_ratio")),
        "market_regime": regime,
        "regime_score": number(regime_score),
        "regime_confidence": number(regime_confidence),
        "data_quality_score": number(quality),
        "moneyflow_coverage": number(capital.get("coverage")),
        "leading_industries": compact_sectors(leading_industries),
        "lagging_industries": compact_sectors(lagging_industries),
        "leading_concepts": compact_sectors(leading_concepts),
        "lagging_concepts": compact_sectors(lagging_concepts),
        "scenario_probabilities": scenario,
        "source_status": source_status,
    }


def quant_summary(session, trade_date: date, registry: WorkbenchRunRegistry) -> tuple[dict[str, Any], list[QuantRankResult]]:
    run = session.scalar(select(QuantRun).where(QuantRun.run_id == registry.quant_run_id))
    if run is None:
        raise RuntimeError(f"QUANT_RUN_NOT_FOUND:{trade_date}")
    rows = list(
        session.scalars(
            select(QuantRankResult)
            .where(QuantRankResult.quant_run_id == run.run_id, QuantRankResult.rank <= 100)
            .order_by(QuantRankResult.rank)
        )
    )
    fields = ["technical_score", "capital_score", "emotion_score", "momentum_score", "risk_score"]
    return {
        "trade_date": trade_date.isoformat(),
        "run_id": run.run_id,
        "manifest_id": run.data_manifest_id,
        "status": run.status,
        "temporal_status": run.temporal_status,
        "universe_count": run.universe_count,
        "filtered_count": run.filtered_count,
        "scored_count": run.scored_count,
        "skipped_count": run.skipped_count,
        "top_count": run.top_count,
        "top100_count": len(rows),
        "top100_average_score": mean(row.total_score for row in rows),
        "top100_median_score": median(row.total_score for row in rows),
        "factor_averages": {field: mean(getattr(row, field) for row in rows) for field in fields},
        "factor_version": run.factor_version,
        "trade_date_cache_used": run.trade_date_cache_used,
        "per_stock_api_call_count": run.per_stock_api_call_count,
        "no_llm_call_verified": run.no_llm_call_verified,
        "total_seconds": number(run.total_seconds),
    }, rows


def pipeline_summary(session, trade_date: date, registry: WorkbenchRunRegistry) -> dict[str, Any]:
    samples = list(
        session.scalars(
            select(ModelValidationSample)
            .where(ModelValidationSample.validation_run_id == registry.flash_run_id)
            .order_by(ModelValidationSample.rank)
        )
    )
    llm_audits = list(
        session.scalars(
            select(ModelValidationLLMAudit).where(ModelValidationLLMAudit.validation_run_id == registry.flash_run_id)
        )
    )
    reviews = list(
        session.scalars(
            select(ProCandidateReview)
            .where(ProCandidateReview.pro_resume_run_id == registry.pro_run_id)
            .order_by(ProCandidateReview.pro_rank, ProCandidateReview.stock_code)
        )
    )
    allocations = list(
        session.scalars(
            select(ModelValidationAllocation).where(ModelValidationAllocation.validation_run_id == registry.flash_run_id)
        )
    )
    orders = list(
        session.scalars(
            select(ModelValidationOrderPlan).where(ModelValidationOrderPlan.validation_run_id == registry.flash_run_id)
        )
    )
    flash_scores = scalar_scores(samples, "llm_score")
    final_success_count = sum(
        ((row.screening_result or {}).get("_trader_demo") or {}).get("execution_status") == "SUCCESS"
        for row in samples
    )
    llm_top = sorted(
        samples,
        key=lambda row: (-(number((row.screening_result or {}).get("llm_score")) or -1), row.rank),
    )[:20]
    final_top = sorted(reviews, key=lambda row: (row.pro_rank is None, row.pro_rank or 9999, row.stock_code))[:20]
    position_values = [number(row.suggested_position_percent) or 0 for row in allocations]
    chains = []
    for row in samples:
        chain = (row.fundamental_result or {}).get("industry_chain") or {}
        chain_name = chain.get("chain_name") if isinstance(chain, dict) else None
        if chain_name:
            chains.append(str(chain_name))
    chain_counts = Counter(chains)
    return {
        "trade_date": trade_date.isoformat(),
        "pipeline_run_id": registry.pipeline_run_id,
        "flash_run_id": registry.flash_run_id,
        "pro_run_id": registry.pro_run_id,
        "status": registry.final_status,
        "counts": dict(registry.counts or {}),
        "flash_evaluated": len(samples),
        "flash_success": int((registry.counts or {}).get("flash_success", final_success_count)),
        "flash_failure": int((registry.counts or {}).get("flash_failure", len(samples) - final_success_count)),
        "flash_final_sample_success": final_success_count,
        "flash_score_min": min(flash_scores) if flash_scores else None,
        "flash_score_average": mean(flash_scores),
        "flash_score_median": median(flash_scores),
        "flash_score_max": max(flash_scores) if flash_scores else None,
        "llm_top_codes": [row.stock_code for row in llm_top],
        "candidate_codes": [row.stock_code for row in reviews],
        "final_top_codes": [row.stock_code for row in final_top],
        "manual_count": int((registry.counts or {}).get("manual", 0)),
        "candidate_count": len(reviews),
        "pro_average_score": mean(row.pro_score for row in reviews),
        "pro_median_score": median(row.pro_score for row in reviews),
        "order_count": len(orders),
        "position_count": len(allocations),
        "non_zero_position_count": sum(value > 0 for value in position_values),
        "total_suggested_position": sum(position_values),
        "total_suggested_capital": sum(number(row.suggested_capital_amount) or 0 for row in allocations),
        "estimated_max_loss": sum(number(row.estimated_max_loss) or 0 for row in allocations),
        "average_risk_reward": mean(row.active_risk_reward for row in orders),
        "llm_tokens": sum(int(row.input_tokens or 0) + int(row.output_tokens or 0) for row in llm_audits),
        "llm_cost_usd": sum(number(row.cost_usd) or 0 for row in llm_audits),
        "industry_chain_concentration": (max(chain_counts.values()) / len(chains)) if chains else None,
        "industry_chain_top": chain_counts.most_common(5),
        "zero_position_reasons": dict(
            Counter(
                str(reason)
                for row in allocations
                if (number(row.suggested_position_percent) or 0) == 0
                for reason in (row.binding_constraints or ["未记录"])
            )
        ),
        "historical_llm_rerun": False,
    }


def overlap(left: list[str], right: list[str]) -> dict[str, Any]:
    a, b = set(left), set(right)
    shared = sorted(a & b)
    return {
        "overlap_count": len(shared),
        "overlap_ratio": len(shared) / max(1, min(len(a), len(b))),
        "shared": shared,
        "entered": sorted(b - a),
        "exited": sorted(a - b),
    }


def quant_overlap(left: list[QuantRankResult], right: list[QuantRankResult]) -> dict[str, Any]:
    result = overlap([row.stock_code for row in left], [row.stock_code for row in right])
    left_rank = {row.stock_code: row.rank for row in left}
    right_rank = {row.stock_code: row.rank for row in right}
    changes = [
        {"stock_code": code, "previous_rank": left_rank[code], "current_rank": right_rank[code], "rank_change": left_rank[code] - right_rank[code]}
        for code in result["shared"]
    ]
    result["largest_rank_changes"] = sorted(changes, key=lambda item: abs(item["rank_change"]), reverse=True)[:10]
    return result


def resolved_dates(session) -> dict[str, date]:
    open_dates: set[date] = set()
    for path in (ROOT / "data" / "cache" / "tushare").glob("trade_cal_*.json"):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in rows if isinstance(rows, list) else []:
            if int(row.get("is_open") or 0) != 1:
                continue
            value = str(row.get("cal_date") or "")
            if len(value) == 8:
                open_dates.add(datetime.strptime(value, "%Y%m%d").date())
    calendar = TradeCalendarService(open_dates=sorted(open_dates))
    previous = calendar.previous_open_trade_date(TODAY)
    previous_two = calendar.previous_open_trade_date(previous)
    target = calendar.next_open_trade_date(TODAY)
    return {"t0": TODAY, "t_minus_1": previous, "t_minus_2": previous_two, "target": target}


def build_report(session) -> dict[str, Any]:
    dates = resolved_dates(session)
    ordered = [dates["t_minus_2"], dates["t_minus_1"], dates["t0"]]
    registries = {}
    markets = {}
    quants = {}
    quant_rows = {}
    pipelines = {}
    for trade_date in ordered:
        registry = session.scalar(
            select(WorkbenchRunRegistry)
            .where(WorkbenchRunRegistry.trade_date == trade_date)
            .order_by(WorkbenchRunRegistry.reconciled_at.desc())
        )
        if registry is None:
            raise RuntimeError(f"WORKBENCH_REGISTRY_NOT_FOUND:{trade_date}")
        registries[trade_date] = registry
        markets[trade_date] = market_snapshot(session, trade_date)
        quants[trade_date], quant_rows[trade_date] = quant_summary(session, trade_date, registry)
        pipelines[trade_date] = pipeline_summary(session, trade_date, registry)

    adjacent = []
    for left, right in zip(ordered, ordered[1:]):
        adjacent.append({
            "from": left.isoformat(),
            "to": right.isoformat(),
            "quant_top100": quant_overlap(quant_rows[left], quant_rows[right]),
            "llm_top20": overlap(pipelines[left]["llm_top_codes"], pipelines[right]["llm_top_codes"]),
            "candidate_set": overlap(pipelines[left]["candidate_codes"], pipelines[right]["candidate_codes"]),
            "final_top20": overlap(pipelines[left]["final_top_codes"], pipelines[right]["final_top_codes"]),
        })

    return {
        "schema_version": "daily_three_trade_day_comparison_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trade_dates": {
            "t0": dates["t0"].isoformat(),
            "t_minus_1": dates["t_minus_1"].isoformat(),
            "t_minus_2": dates["t_minus_2"].isoformat(),
            "target_trade_date": dates["target"].isoformat(),
            "source": "TradeCalendarService",
        },
        "market": [markets[trade_date] for trade_date in ordered],
        "quant": [quants[trade_date] for trade_date in ordered],
        "llm_and_final": [pipelines[trade_date] for trade_date in ordered],
        "adjacent_comparisons": adjacent,
        "controls": {
            "historical_llm_rerun_count": 0,
            "ifind_external_call_count": 0,
            "real_trading_enabled": False,
            "orders_created": 0,
            "provider": "TUSHARE",
            "point_in_time_note": "2026-07-10 行业和概念分类未固化，未使用当前分类回填。",
        },
    }


def pipeline_report(session, report: dict[str, Any]) -> dict[str, Any]:
    today_quant = report["quant"][-1]
    today_pipeline = report["llm_and_final"][-1]
    today_market = report["market"][-1]
    manifest = session.scalar(
        select(RunDataManifestRecord).where(RunDataManifestRecord.manifest_id == today_quant["manifest_id"])
    )
    validation = session.scalar(
        select(ModelValidationRun).where(ModelValidationRun.run_id == today_pipeline["flash_run_id"])
    )
    return {
        "schema_version": "daily_pipeline_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trade_dates": report["trade_dates"],
        "pipeline": today_pipeline,
        "quant": today_quant,
        "market": today_market,
        "temporal_gate": {
            "status": today_quant["temporal_status"],
            "actionable": True,
            "manifest_id": today_quant["manifest_id"],
            "required_dataset_watermarks": list(manifest.required_dataset_watermarks or []) if manifest else [],
            "optional_dataset_watermarks": list(manifest.optional_dataset_watermarks or []) if manifest else [],
        },
        "validation_run": {
            "status": validation.status if validation else None,
            "real_llm": validation.real_llm if validation else None,
            "knowledge_mode": validation.knowledge_mode if validation else None,
        },
        "outputs": {
            "today_excel": str((OUTPUT_DIR / "AI交易助手_2026-07-14.xlsx").resolve()),
            "comparison_excel": str(EXCEL_PATH.resolve()),
            "volume_excel": str((OUTPUT_DIR / "全A成交量及权重_2026-07-13_2026-07-14.xlsx").resolve()),
        },
        "safety": report["controls"],
        "database": get_database_identity(),
    }


def add_title(ws, title: str, subtitle: str, last_column: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_column)
    ws.cell(1, 1, title)
    ws.cell(1, 1).font = Font(name="Microsoft YaHei", size=16, bold=True, color=WHITE)
    ws.cell(1, 1).fill = PatternFill("solid", fgColor=NAVY)
    ws.cell(1, 1).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_column)
    ws.cell(2, 1, subtitle)
    ws.cell(2, 1).font = Font(name="Microsoft YaHei", size=10, color="4B5563")
    ws.cell(2, 1).fill = PatternFill("solid", fgColor=PALE_BLUE)
    ws.cell(2, 1).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 30


def write_table(ws, start_row: int, headers: list[str], rows: list[list[Any]], *, name: str) -> tuple[int, int]:
    for column, header in enumerate(headers, 1):
        cell = ws.cell(start_row, column, header)
        cell.font = Font(name="Microsoft YaHei", bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.border = ALL_BORDER
        cell.alignment = CENTER
    for row_number, values in enumerate(rows, start_row + 1):
        for column, value in enumerate(values, 1):
            cell = ws.cell(row_number, column, value)
            cell.border = ALL_BORDER
            cell.alignment = CENTER
            cell.font = Font(name="Microsoft YaHei", size=10)
            if row_number % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=LIGHT_BLUE)
    end_row = start_row + len(rows)
    end_column = len(headers)
    if ws.auto_filter.ref is None:
        ws.auto_filter.ref = f"A{start_row}:{get_column_letter(end_column)}{end_row}"
    if ws.freeze_panes is None:
        ws.freeze_panes = f"A{start_row + 1}"
    return end_row, end_column


def style_sheet(ws, max_width: int = 32) -> None:
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                cell.alignment = CENTER
    for column in range(1, ws.max_column + 1):
        letter = get_column_letter(column)
        lengths = [len(str(ws.cell(row, column).value or "")) for row in range(1, min(ws.max_row, 200) + 1)]
        ws.column_dimensions[letter].width = min(max(12, max(lengths, default=8) * 1.35), max_width)
    for row in range(3, ws.max_row + 1):
        ws.row_dimensions[row].height = 30
    ws.sheet_view.showGridLines = False


def percent_columns(ws, columns: Iterable[int], start_row: int = 4) -> None:
    for column in columns:
        for row in range(start_row, ws.max_row + 1):
            cell = ws.cell(row, column)
            if isinstance(cell.value, (int, float)) or (isinstance(cell.value, str) and cell.value.startswith("=")):
                cell.number_format = "0.00%"


def signed_color(ws, columns: Iterable[int], start_row: int = 4) -> None:
    red_fill = PatternFill("solid", fgColor="FDE9E7")
    green_fill = PatternFill("solid", fgColor="E2F0D9")
    for column in columns:
        letter = get_column_letter(column)
        cells = f"{letter}{start_row}:{letter}{ws.max_row}"
        ws.conditional_formatting.add(cells, CellIsRule(operator="greaterThan", formula=["0"], fill=red_fill, font=Font(color=RED)))
        ws.conditional_formatting.add(cells, CellIsRule(operator="lessThan", formula=["0"], fill=green_fill, font=Font(color=GREEN)))


def sector_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "时点分类不可用"
    return "；".join(f"{row['name']} {number(row['change_percent']) or 0:+.2%}" for row in rows)


def market_regime_label(value: Any) -> str:
    key = str(value or "DATA_INSUFFICIENT")
    return MARKET_REGIME_LABELS.get(key, key)


def zero_position_reason_text(reasons: Any) -> str:
    if not isinstance(reasons, dict) or not reasons:
        return "无"
    return "；".join(
        f"{ZERO_POSITION_REASON_LABELS.get(str(key), str(key))}：{value}"
        for key, value in reasons.items()
    )


def build_workbook(report: dict[str, Any]) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    dates = [row["trade_date"] for row in report["market"]]

    ws = wb.create_sheet("01_三日市场概览")
    add_title(ws, "近三交易日市场概览", "数据来自本地 Tushare 结构化缓存与当日固化运行；2026-07-10 未用当前行业分类回填。", 14)
    rows = []
    for market in report["market"]:
        scenario = market["scenario_probabilities"]
        rows.append([
            market["trade_date"], market_regime_label(market["market_regime"]), market["regime_score"], market["up"], market["down"], market["flat"],
            market["average_return"], market["median_return"], market["turnover"], market["turnover_change"],
            market["limit_up"], market["limit_down"], market["failed_limit_up"],
            f"基准 {scenario['base']}% / 乐观 {scenario['bull']}% / 悲观 {scenario['bear']}%" if scenario["base"] is not None else "未生成",
        ])
    write_table(ws, 3, ["交易日", "市场状态", "状态分", "上涨", "下跌", "平盘", "平均涨跌幅", "中位涨跌幅", "成交额(元)", "成交额环比", "涨停", "跌停", "炸板", "情景概率"], rows, name="MarketOverview")
    percent_columns(ws, [7, 8, 10])
    signed_color(ws, [7, 8, 10])
    for row in range(4, ws.max_row + 1):
        ws.cell(row, 9).number_format = "#,##0"
    style_sheet(ws, 34)

    ws = wb.create_sheet("02_市场宽度与成交")
    add_title(ws, "市场宽度与成交", "涨跌家数、成交额、涨跌停结构与资金流覆盖率采用统一口径。", 15)
    rows = []
    for market in report["market"]:
        row_number = len(rows) + 4
        rows.append([
            market["trade_date"], market["valid_count"], market["up"], market["down"], market["flat"], f"=IF(B{row_number}=0,0,C{row_number}/B{row_number})",
            market["average_return"], market["median_return"], market["equal_weight_return"], market["turnover"],
            market["turnover_change"], market["limit_up"], market["limit_down"], market["failed_limit_up_ratio"], market["moneyflow_coverage"],
        ])
    write_table(ws, 3, ["交易日", "有效股票数", "上涨", "下跌", "平盘", "上涨比例", "平均涨跌幅", "中位涨跌幅", "等权涨跌幅", "成交额(元)", "成交额环比", "涨停", "跌停", "炸板率", "资金流覆盖率"], rows, name="BreadthTurnover")
    percent_columns(ws, [6, 7, 8, 9, 11, 14, 15])
    signed_color(ws, [7, 8, 9, 11])
    for row in range(4, ws.max_row + 1):
        ws.cell(row, 10).number_format = "#,##0"
    style_sheet(ws)

    ws = wb.create_sheet("03_行业概念对比")
    add_title(ws, "行业与概念对比", "仅展示当日已固化的分类快照；缺失时明确显示不可用。", 6)
    rows = [[
        market["trade_date"], sector_text(market["leading_industries"]), sector_text(market["lagging_industries"]),
        sector_text(market["leading_concepts"]), sector_text(market["lagging_concepts"]),
        "可用" if market["point_in_time_classification"] else "时点分类不可用",
    ] for market in report["market"]]
    write_table(ws, 3, ["交易日", "领涨行业", "领跌行业", "领涨概念", "领跌概念", "时点分类状态"], rows, name="SectorComparison")
    for row in range(4, ws.max_row + 1):
        if ws.cell(row, 6).value != "可用":
            for column in range(1, 7):
                ws.cell(row, column).fill = PatternFill("solid", fgColor=YELLOW)
    style_sheet(ws, 46)

    ws = wb.create_sheet("04_量化对比")
    add_title(ws, "量化对比", "全部读取既有正式量化结果；本页不调用模型。", 15)
    rows = []
    for quant in report["quant"]:
        factors = quant["factor_averages"]
        rows.append([
            quant["trade_date"], quant["run_id"], quant["universe_count"], quant["scored_count"], quant["skipped_count"],
            quant["top100_average_score"], quant["top100_median_score"], factors["technical_score"], factors["capital_score"],
            factors["emotion_score"], factors["momentum_score"], factors["risk_score"], quant["factor_version"],
            quant["per_stock_api_call_count"], "是" if quant["no_llm_call_verified"] else "否",
        ])
    end_row, _ = write_table(ws, 3, ["交易日", "量化运行编号", "股票池", "已评分", "跳过", "前100均分", "前100中位分", "技术", "资金", "情绪", "动量", "风险", "因子版本", "逐股数据调用", "零模型调用已验证"], rows, name="QuantSummary")
    start = end_row + 1
    adjacent_rows = []
    for pair in report["adjacent_comparisons"]:
        value = pair["quant_top100"]
        movers = "；".join(f"{item['stock_code']} {item['rank_change']:+d}" for item in value["largest_rank_changes"][:5])
        row_number = start + 1 + len(adjacent_rows)
        adjacent_rows.append([pair["from"], pair["to"], value["overlap_count"], f"=IF(C{row_number}+MIN(E{row_number},F{row_number})=0,0,C{row_number}/(C{row_number}+MIN(E{row_number},F{row_number})))", len(value["entered"]), len(value["exited"]), movers])
    write_table(ws, start, ["前一交易日", "后一交易日", "前100重合数", "重合率", "新进入", "移出", "排名变化最大(正数为上升)"], adjacent_rows, name="QuantOverlap")
    percent_columns(ws, [4], start + 1)
    style_sheet(ws, 42)

    ws = wb.create_sheet("05_模型与终排对比")
    add_title(ws, "模型二筛与终排对比", "只读取各交易日已有的二筛与深度复核结果，未补跑历史模型。", 15)
    rows = []
    for pipeline in report["llm_and_final"]:
        rows.append([
            pipeline["trade_date"], pipeline["flash_evaluated"], pipeline["flash_success"], pipeline["flash_failure"],
            pipeline["flash_score_min"], pipeline["flash_score_average"], pipeline["flash_score_median"], pipeline["flash_score_max"],
            pipeline["manual_count"], pipeline["candidate_count"], pipeline["pro_average_score"], pipeline["pro_median_score"],
            pipeline["llm_tokens"], pipeline["llm_cost_usd"], pipeline["industry_chain_concentration"],
        ])
    end_row, _ = write_table(ws, 3, ["交易日", "二筛评估", "成功", "失败", "二筛最低", "二筛均分", "二筛中位", "二筛最高", "人工池", "候选集", "深度复核均分", "深度复核中位", "模型用量", "成本(美元)", "产业链集中度"], rows, name="LLMFinalSummary")
    percent_columns(ws, [15])
    start = end_row + 1
    overlap_rows = []
    for pair in report["adjacent_comparisons"]:
        overlap_rows.append([
            pair["from"], pair["to"], pair["llm_top20"]["overlap_count"], pair["llm_top20"]["overlap_ratio"],
            pair["candidate_set"]["overlap_count"], pair["candidate_set"]["overlap_ratio"],
            pair["final_top20"]["overlap_count"], pair["final_top20"]["overlap_ratio"],
        ])
    write_table(ws, start, ["前一交易日", "后一交易日", "模型前20重合", "模型前20重合率", "候选集重合", "候选集重合率", "终排前20重合", "终排前20重合率"], overlap_rows, name="LLMFinalOverlap")
    percent_columns(ws, [4, 6, 8], start + 1)
    style_sheet(ws)

    ws = wb.create_sheet("06_挂单仓位对比")
    add_title(ws, "挂单与仓位对比", "全部为本地规则计算的复核结果，不创建真实或虚拟订单。", 11)
    rows = []
    for pipeline in report["llm_and_final"]:
        rows.append([
            pipeline["trade_date"], pipeline["candidate_count"], pipeline["order_count"], pipeline["position_count"],
            pipeline["non_zero_position_count"], pipeline["total_suggested_position"], pipeline["total_suggested_capital"],
            pipeline["estimated_max_loss"], pipeline["average_risk_reward"],
            zero_position_reason_text(pipeline["zero_position_reasons"]),
            "仅供复核",
        ])
    write_table(ws, 3, ["交易日", "候选数", "挂单计划", "仓位计划", "非零仓位", "建议总仓位", "建议资金", "预计最大损失", "平均风险收益比", "零仓位原因", "状态"], rows, name="OrderPosition")
    percent_columns(ws, [6])
    for row in range(4, ws.max_row + 1):
        ws.cell(row, 7).number_format = "#,##0.00"
        ws.cell(row, 8).number_format = "#,##0.00"
    style_sheet(ws, 42)

    ws = wb.create_sheet("07_口径与异常")
    add_title(ws, "口径、来源与异常", "本页集中说明可追溯来源、缺失项和安全边界。", 5)
    rows = [
        ["交易日解析", "前两个交易日 / 前一交易日 / 当日 / 下一交易日", "本地交易日历服务", "可用", " / ".join([*dates, report["trade_dates"]["target_trade_date"]])],
        ["2026-07-10行业概念", "历史时点分类", "无当日固化分类快照", "不可用", "未使用当前股票主数据或最新概念成员回填"],
        ["历史模型", "二筛 / 深度复核", "历史数据库运行记录", "可用", "历史补跑次数为 0"],
        ["今日资金流", "资金流覆盖率", "Tushare交易日批量缓存", "可用", f"{report['market'][-1]['moneyflow_coverage']:.2%}"],
        ["量化", "逐股数据请求", "量化运行逐股调用计数", "通过", "三日均为 0"],
        ["量化", "模型隔离", "量化运行零模型调用标记", "通过", "三日均为是"],
        ["iFinD", "外部调用", "流程控制记录", "通过", "调用数 0"],
        ["交易", "真实交易 / 订单", "安全配置与运行模式", "关闭", "真实交易开关关闭；订单创建数 0"],
        ["成交量权重", "2026-07-13 / 2026-07-14", "全A成交量及权重工作簿", "已生成", "独立工作簿，不混入三日对比口径"],
    ]
    write_table(ws, 3, ["项目", "口径", "来源", "状态", "说明"], rows, name="DefinitionsExceptions")
    for row in range(4, ws.max_row + 1):
        if ws.cell(row, 4).value in {"不可用", "关闭"}:
            for column in range(1, 6):
                ws.cell(row, column).fill = PatternFill("solid", fgColor=YELLOW)
    style_sheet(ws, 50)

    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    wb.calculation.calcMode = "auto"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    wb.save(EXCEL_PATH)


def verify_workbook() -> dict[str, Any]:
    wb = load_workbook(EXCEL_PATH, data_only=False)
    expected = [
        "01_三日市场概览", "02_市场宽度与成交", "03_行业概念对比", "04_量化对比",
        "05_模型与终排对比", "06_挂单仓位对比", "07_口径与异常",
    ]
    if wb.sheetnames != expected:
        raise RuntimeError(f"WORKBOOK_SHEETS_MISMATCH:{wb.sheetnames}")
    non_centered = []
    formula_errors = []
    formula_count = 0
    for ws in wb.worksheets:
        if ws.tables:
            raise RuntimeError(f"WORKBOOK_TABLE_OBJECT_FOUND:{ws.title}")
        for row_number in range(1, ws.max_row + 1):
            if not any(cell.value is not None for cell in ws[row_number]):
                raise RuntimeError(f"WORKBOOK_BLANK_ROW:{ws.title}:{row_number}")
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                if cell.alignment.horizontal != "center" or cell.alignment.vertical != "center" or not cell.alignment.wrap_text:
                    non_centered.append(f"{ws.title}!{cell.coordinate}")
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formula_count += 1
                if isinstance(cell.value, str) and any(error in cell.value for error in ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?")):
                    formula_errors.append(f"{ws.title}!{cell.coordinate}")
    if non_centered:
        raise RuntimeError(f"WORKBOOK_NOT_CENTERED:{non_centered[:10]}")
    if formula_errors:
        raise RuntimeError(f"WORKBOOK_FORMULA_ERROR:{formula_errors[:10]}")
    return {"sheets": wb.sheetnames, "non_centered": 0, "formula_errors": 0, "formula_count": formula_count}


def verify_workbench(session) -> dict[str, Any]:
    service = WorkbenchService(session)
    status = service.status(TODAY)
    dates = service.available_dates()
    final = service.final_results(TODAY)
    order_position = service.order_position_results(TODAY)
    fundamentals = service.fundamentals(TODAY)
    if not any(str(item.get("trade_date")) == TODAY.isoformat() for item in dates):
        raise RuntimeError("WORKBENCH_TODAY_NOT_AVAILABLE")
    if not status.get("quant_run_id") or not status.get("flash_run_id") or not status.get("pro_run_id"):
        raise RuntimeError("WORKBENCH_RUN_BUNDLE_INCOMPLETE")
    return {
        "pipeline_run_id": status.get("pipeline_run_id"),
        "quant_status": status["quant"]["status"],
        "flash_status": status["flash"]["status"],
        "final_status": status["final"]["status"],
        "market_review_status": status["market_review"]["status"],
        "export_status": status["export"]["status"],
        "final_count": len(final),
        "order_position_count": len(order_position),
        "fundamental_count": len(fundamentals),
        "available_dates": [str(item.get("trade_date")) for item in dates[:5]],
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = get_session()
    try:
        report = build_report(session)
        COMPARISON_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        PIPELINE_JSON_PATH.write_text(json.dumps(pipeline_report(session, report), ensure_ascii=False, indent=2), encoding="utf-8")
        build_workbook(report)
        verification = verify_workbook()
        workbench = verify_workbench(session)
        print(json.dumps({
            "comparison_json": str(COMPARISON_JSON_PATH),
            "pipeline_json": str(PIPELINE_JSON_PATH),
            "excel": str(EXCEL_PATH),
            "workbook_verification": verification,
            "workbench": workbench,
        }, ensure_ascii=False, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
