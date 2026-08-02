from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.ranking_evaluation import (  # noqa: E402
    ModelEffectivenessDailyMetric,
    ModelEffectivenessStageSnapshot,
    RankingEvaluationForwardOutcome,
    RankingEvaluationSnapshot,
    RankingEvaluationSnapshotItem,
)
from database.session import get_session, init_db  # noqa: E402
from services.ranking_evaluation.constants import EVALUATION_SCOPE  # noqa: E402
from services.ranking_evaluation.model_stage_service import (  # noqa: E402
    ModelStageEffectivenessService,
)
from services.ranking_evaluation.outcome_backfill_service import (  # noqa: E402
    OutcomeBackfillService,
)
from services.ranking_evaluation.schemas import (  # noqa: E402
    RankingSourceRow,
    SnapshotCaptureRequest,
)
from services.ranking_evaluation.snapshot_service import RankingSnapshotService  # noqa: E402
from services.ranking_evaluation.utils import stable_hash, write_immutable_text  # noqa: E402


FACTOR_VERSION = "TUSHARE_QUANT_V2_CORRECTED_SHADOW"
FLASH_VERSION = "FLASH_V2_STRUCTURED_LIGHT_SCREENING_V5"
HORIZONS = (1, 3, 5, 10)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def _num(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.4f}"


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return fmean(values) if values else None


def _read_frozen_rows(trade_date: date) -> tuple[list[RankingSourceRow], Path, dict]:
    root = ROOT / "outputs" / "quant_v2_validation" / trade_date.isoformat()
    csv_path = root / "v2_full_universe.csv"
    payload_path = root / "quant_v2_workbook_payload.json"
    if not csv_path.exists() or not payload_path.exists():
        raise FileNotFoundError(f"FROZEN_V2_ARTIFACT_MISSING:{trade_date}")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    rows: list[RankingSourceRow] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for source_row_number, row in enumerate(csv.DictReader(handle), start=1):
            rank = int(row["rank"])
            if rank > 100:
                continue
            rows.append(
                RankingSourceRow(
                    stock_code=row["stock_code"],
                    stock_name=row.get("stock_name") or row["stock_code"],
                    original_rank=rank,
                    quant_score=float(row["total_score"]),
                    ts_code=row["stock_code"],
                    source_row_number=source_row_number,
                    raw_payload={
                        "frozen_artifact": str(csv_path.resolve()),
                        "factor_version": row.get("factor_version"),
                    },
                )
            )
    rows.sort(key=lambda item: item.original_rank)
    if len(rows) != 100 or [row.original_rank for row in rows] != list(range(1, 101)):
        raise ValueError(f"FROZEN_TOP100_INVALID:{trade_date}:rows={len(rows)}")
    return rows, csv_path, payload


def _ensure_quant_snapshot(session, trade_date: date) -> dict[str, Any]:
    existing = session.scalar(
        select(RankingEvaluationSnapshot).where(
            RankingEvaluationSnapshot.ranking_trade_date == trade_date,
            RankingEvaluationSnapshot.factor_version == FACTOR_VERSION,
            RankingEvaluationSnapshot.evaluation_scope == EVALUATION_SCOPE,
        )
    )
    if existing:
        return {
            "status": "EXISTING_IMMUTABLE_SNAPSHOT",
            "snapshot_id": existing.snapshot_id,
        }
    rows, csv_path, payload = _read_frozen_rows(trade_date)
    generated_at = datetime.fromtimestamp(csv_path.stat().st_mtime, tz=timezone.utc)
    request = SnapshotCaptureRequest(
        ranking_trade_date=trade_date,
        source_quant_run_id=str(payload.get("run_id") or f"frozen-v2-{trade_date}"),
        factor_version=FACTOR_VERSION,
        evaluation_scope=EVALUATION_SCOPE,
        allow_historical_import=True,
        snapshot_origin="HISTORICAL_IMPORT",
        generated_at=generated_at,
    )
    return RankingSnapshotService(session).capture_rows(
        request,
        rows,
        source_input_hash=_sha256(csv_path),
        model_name=FACTOR_VERSION,
        score_version=FACTOR_VERSION,
        production_or_shadow="SHADOW",
    )


def _stage_for(session, trade_date: date, stage_type: str):
    return session.scalar(
        select(ModelEffectivenessStageSnapshot).where(
            ModelEffectivenessStageSnapshot.ranking_trade_date == trade_date,
            ModelEffectivenessStageSnapshot.quant_factor_version == FACTOR_VERSION,
            ModelEffectivenessStageSnapshot.stage_type == stage_type,
        )
    )


def _capture_stages(session, trade_date: date) -> list[dict[str, Any]]:
    service = ModelStageEffectivenessService(session)
    results = []
    if not _stage_for(session, trade_date, "QUANT"):
        results.append(
            service.capture_quant_cohort(
                trade_date=trade_date,
                factor_version=FACTOR_VERSION,
            )
        )
    checkpoint = (
        ROOT
        / "outputs"
        / "quant_v2_validation"
        / trade_date.isoformat()
        / ".monday_v2_llm_checkpoint.json"
    )
    if checkpoint.exists() and not _stage_for(session, trade_date, "FLASH_V2"):
        results.append(
            service.capture_v2_checkpoint(
                trade_date=trade_date,
                checkpoint_path=checkpoint,
                audit_path=checkpoint.parent / "monday_v2_candidate_audit.json",
            )
        )
    return results


def _refresh_stage_metrics(session, dates: list[date]) -> None:
    service = ModelStageEffectivenessService(session)
    stages = list(
        session.scalars(
            select(ModelEffectivenessStageSnapshot).where(
                ModelEffectivenessStageSnapshot.ranking_trade_date.in_(dates),
                ModelEffectivenessStageSnapshot.quant_factor_version == FACTOR_VERSION,
                ModelEffectivenessStageSnapshot.stage_type.in_(("QUANT", "FLASH_V2")),
            )
        )
    )
    for stage in stages:
        for horizon in HORIZONS:
            service.calculate_daily_metrics(stage.id, horizon=horizon)


def _outcome_due_map(session, dates: list[date]) -> dict[tuple[date, int], date]:
    rows = session.execute(
        select(
            RankingEvaluationSnapshot.ranking_trade_date,
            RankingEvaluationForwardOutcome.horizon,
            RankingEvaluationForwardOutcome.due_trade_date,
        )
        .join(
            RankingEvaluationSnapshotItem,
            RankingEvaluationSnapshotItem.snapshot_id
            == RankingEvaluationSnapshot.id,
        )
        .join(
            RankingEvaluationForwardOutcome,
            RankingEvaluationForwardOutcome.snapshot_item_id
            == RankingEvaluationSnapshotItem.id,
        )
        .where(
            RankingEvaluationSnapshot.ranking_trade_date.in_(dates),
            RankingEvaluationSnapshot.factor_version == FACTOR_VERSION,
        )
        .distinct()
    ).all()
    return {(ranking_date, int(horizon)): due for ranking_date, horizon, due in rows}


def _collect(session, dates: list[date], cutoff: date):
    stages = list(
        session.scalars(
            select(ModelEffectivenessStageSnapshot)
            .where(
                ModelEffectivenessStageSnapshot.ranking_trade_date.in_(dates),
                ModelEffectivenessStageSnapshot.quant_factor_version == FACTOR_VERSION,
                ModelEffectivenessStageSnapshot.stage_type.in_(("QUANT", "FLASH_V2")),
            )
            .order_by(
                ModelEffectivenessStageSnapshot.ranking_trade_date,
                ModelEffectivenessStageSnapshot.stage_type,
            )
        )
    )
    due_map = _outcome_due_map(session, dates)
    coverage = []
    for ranking_date in dates:
        quant = next(
            (row for row in stages if row.ranking_trade_date == ranking_date and row.stage_type == "QUANT"),
            None,
        )
        flash = next(
            (row for row in stages if row.ranking_trade_date == ranking_date and row.stage_type == "FLASH_V2"),
            None,
        )
        available = [
            f"D{horizon}"
            for horizon in HORIZONS
            if due_map.get((ranking_date, horizon))
            and due_map[(ranking_date, horizon)] <= cutoff
        ]
        coverage.append(
            {
                "recommendation_date": ranking_date.isoformat(),
                "quant_top100": int((quant.reliability_json or {}).get("input_count") or 0)
                if quant
                else 0,
                "flash_evaluated": int((flash.reliability_json or {}).get("input_count") or 0)
                if flash
                else 0,
                "flash_selected": int((flash.reliability_json or {}).get("selected_count") or 0)
                if flash
                else 0,
                "flash_coverage": (
                    float((flash.reliability_json or {}).get("scoring_coverage_ratio") or 0)
                    if flash
                    else None
                ),
                "cohort_match": bool(flash.cohort_match) if flash else None,
                "actionability": flash.actionability_status if flash else "NO_FLASH_SNAPSHOT",
                "matured_by_cutoff": ",".join(available) or "NONE",
            }
        )
    metrics = list(
        session.scalars(
            select(ModelEffectivenessDailyMetric)
            .where(
                ModelEffectivenessDailyMetric.ranking_trade_date.in_(dates),
                ModelEffectivenessDailyMetric.stage_type.in_(("QUANT", "FLASH_V2")),
                ModelEffectivenessDailyMetric.actionable_only.is_(False),
            )
            .order_by(
                ModelEffectivenessDailyMetric.ranking_trade_date,
                ModelEffectivenessDailyMetric.stage_type,
                ModelEffectivenessDailyMetric.horizon,
            )
        )
    )
    daily = []
    for metric in metrics:
        due = due_map.get((metric.ranking_trade_date, metric.horizon))
        if due is None or due > cutoff:
            continue
        payload = metric.metric_payload_json or {}
        daily.append(
            {
                "recommendation_date": metric.ranking_trade_date.isoformat(),
                "outcome_date": due.isoformat(),
                "stage": metric.stage_type,
                "horizon": f"D{metric.horizon}",
                "status": metric.calculation_status,
                "valid_samples": metric.valid_sample_count,
                "rank_ic": payload.get("rank_ic"),
                "quant_score_ic": payload.get("quant_score_ic"),
                "top20_mean_return": payload.get("top20_average_return"),
                "bottom20_mean_return": payload.get("bottom20_average_return"),
                "top_bottom_spread": payload.get("top20_bottom20_spread"),
                "flash_score_ic": payload.get("flash_score_ic"),
                "flash_selected_mean_return": payload.get("selected_mean_return"),
                "flash_unselected_mean_return": payload.get("unselected_mean_return"),
                "flash_selection_spread": payload.get("selection_spread"),
                "quant_top20_mean_return": payload.get("quant_top20_mean_return"),
                "flash_incremental_lift": payload.get("incremental_lift"),
            }
        )
    aggregate = []
    for stage in ("QUANT", "FLASH_V2"):
        for horizon in ("D1", "D3", "D5", "D10"):
            scoped = [
                row
                for row in daily
                if row["stage"] == stage
                and row["horizon"] == horizon
                and row["status"] not in {"NOT_MATURED", "COMPARISON_BLOCKED"}
            ]
            if not scoped:
                continue
            aggregate.append(
                {
                    "stage": stage,
                    "horizon": horizon,
                    "valid_days": len(scoped),
                    "valid_samples": sum(int(row["valid_samples"]) for row in scoped),
                    "mean_rank_ic": _mean(scoped, "rank_ic"),
                    "mean_quant_score_ic": _mean(scoped, "quant_score_ic"),
                    "mean_top20_return": _mean(scoped, "top20_mean_return"),
                    "mean_bottom20_return": _mean(scoped, "bottom20_mean_return"),
                    "mean_top_bottom_spread": _mean(scoped, "top_bottom_spread"),
                    "mean_flash_score_ic": _mean(scoped, "flash_score_ic"),
                    "mean_flash_selected_return": _mean(
                        scoped, "flash_selected_mean_return"
                    ),
                    "mean_flash_incremental_lift": _mean(
                        scoped, "flash_incremental_lift"
                    ),
                    "aggregation": "DAILY_FIRST_EQUAL_WEIGHT",
                }
            )
    return coverage, daily, aggregate


def _report(
    *,
    start: date,
    end: date,
    cutoff: date,
    coverage: list[dict[str, Any]],
    daily: list[dict[str, Any]],
    aggregate: list[dict[str, Any]],
    outcome_result: dict[str, Any],
) -> str:
    lines = [
        "# 本周 V2 Quant + Flash 前向验证（周五盘中截断版）",
        "",
        f"- 推荐日范围：{start.isoformat()} 至 {end.isoformat()}",
        f"- 收益数据截止：{cutoff.isoformat()} 收盘",
        "- 额外纳入：2026-07-24 推荐，因为其 D1/D3 收益发生在本周",
        "- 明确排除：2026-07-31 的行情、推荐、收益",
        "- 收益口径：冻结推荐日收盘价至到期交易日收盘价",
        "- 本次调用：外部 API 0、LLM 0、搜索 0、订单 0",
        "",
        "## 数据与成熟度",
        "",
        "| 推荐日 | Quant Top100 | Flash已评 | Flash选中 | Flash覆盖 | 同队列 | 时效状态 | 截止日已成熟 |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    for row in coverage:
        lines.append(
            "| {recommendation_date} | {quant_top100} | {flash_evaluated} | "
            "{flash_selected} | {coverage} | {match} | {actionability} | {matured} |".format(
                **row,
                coverage=_pct(row["flash_coverage"]),
                match="是" if row["cohort_match"] else "否",
                matured=row["matured_by_cutoff"],
            )
        )
    lines.extend(
        [
            "",
            "## 每日验证",
            "",
            "| 推荐日 | 收益日 | 阶段 | 周期 | 状态 | 样本 | Rank IC | Score IC | Top20 | Bottom20 | 多空差 | Flash增量 |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in daily:
        formatted = {
            **row,
            "rank_ic_display": _num(row["rank_ic"]),
            "score_ic_display": _num(
                row["quant_score_ic"]
                if row["stage"] == "QUANT"
                else row["flash_score_ic"]
            ),
            "top20_display": _pct(
                row["top20_mean_return"]
                if row["stage"] == "QUANT"
                else row["flash_selected_mean_return"]
            ),
            "bottom20_display": _pct(
                row["bottom20_mean_return"]
                if row["stage"] == "QUANT"
                else row["flash_unselected_mean_return"]
            ),
            "spread_display": _pct(
                row["top_bottom_spread"]
                if row["stage"] == "QUANT"
                else row["flash_selection_spread"]
            ),
            "lift_display": _pct(row["flash_incremental_lift"]),
        }
        lines.append(
            "| {recommendation_date} | {outcome_date} | {stage} | {horizon} | "
            "{status} | {valid_samples} | {rank_ic_display} | {score_ic_display} | "
            "{top20_display} | {bottom20_display} | {spread_display} | "
            "{lift_display} |".format(**formatted)
        )
    lines.extend(
        [
            "",
            "## 按交易日等权汇总",
            "",
            "| 阶段 | 周期 | 有效交易日 | 样本 | 平均Rank IC | 平均Score IC | 平均Top20 | 平均Bottom20 | 平均多空差 | Flash平均增量 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in aggregate:
        formatted = {
            **row,
            "rank_ic_display": _num(row["mean_rank_ic"]),
            "score_ic_display": _num(
                row["mean_quant_score_ic"]
                if row["stage"] == "QUANT"
                else row["mean_flash_score_ic"]
            ),
            "top20_display": _pct(
                row["mean_top20_return"]
                if row["stage"] == "QUANT"
                else row["mean_flash_selected_return"]
            ),
            "bottom20_display": _pct(row["mean_bottom20_return"]),
            "spread_display": _pct(row["mean_top_bottom_spread"]),
            "lift_display": _pct(row["mean_flash_incremental_lift"]),
        }
        lines.append(
            "| {stage} | {horizon} | {valid_days} | {valid_samples} | "
            "{rank_ic_display} | {score_ic_display} | {top20_display} | "
            "{bottom20_display} | {spread_display} | {lift_display} |".format(
                **formatted
            )
        )
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- Flash V2 每日输入不足100只，队列与冻结Quant Top100不完全一致；Flash相对Quant的正式增量比较维持 COMPARISON_BLOCKED。",
            "- 周五尚未收盘，因此7月30日推荐的D1、7月28日推荐的D3、7月24日推荐的D5均未计入。",
            "- 有效交易日不足5天，不能计算稳定置信区间，也不能据此宣布模型通过或晋级。",
            "- 当前结论：INSUFFICIENT_DATA；保留Shadow，不修改生产逻辑。",
            "",
            "## 回填审计",
            "",
            "```json",
            json.dumps(outcome_result, ensure_ascii=False, indent=2, default=str),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a partial trading week without using in-progress-day data."
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--cutoff", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--anchor",
        type=date.fromisoformat,
        help="Prior recommendation date whose outcomes mature inside the week.",
    )
    args = parser.parse_args()
    if args.cutoff > args.end:
        raise ValueError("CUTOFF_MUST_NOT_EXCEED_END")
    dates = []
    if args.anchor:
        dates.append(args.anchor)
    current = args.start
    while current <= args.end:
        dates.append(current)
        current = date.fromordinal(current.toordinal() + 1)

    init_db()
    session = get_session()
    try:
        captures = []
        valid_dates = []
        for trade_date in dates:
            try:
                captures.append(_ensure_quant_snapshot(session, trade_date))
                captures.extend(_capture_stages(session, trade_date))
                valid_dates.append(trade_date)
            except FileNotFoundError:
                continue
        outcome = OutcomeBackfillService(session).refresh(
            as_of_date=args.cutoff,
            factor_version=FACTOR_VERSION,
        )
        _refresh_stage_metrics(session, valid_dates)
        coverage, daily, aggregate = _collect(session, valid_dates, args.cutoff)
        material = {
            "start": args.start,
            "end": args.end,
            "cutoff": args.cutoff,
            "anchor": args.anchor,
            "factor_version": FACTOR_VERSION,
            "flash_version": FLASH_VERSION,
            "coverage": coverage,
            "daily": daily,
            "aggregate": aggregate,
            "outcome": outcome,
            "external_api_calls": 0,
            "llm_calls": 0,
            "search_calls": 0,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }
        run_hash = stable_hash(material)
        target = (
            ROOT
            / "outputs"
            / "model_effectiveness"
            / f"partial_week_{args.start.isoformat()}_{args.end.isoformat()}"
            / f"validation-{run_hash[:24]}"
        )
        target.mkdir(parents=True, exist_ok=True)
        report = _report(
            start=args.start,
            end=args.end,
            cutoff=args.cutoff,
            coverage=coverage,
            daily=daily,
            aggregate=aggregate,
            outcome_result=outcome,
        )
        paths = {}
        for filename, content in {
            "validation_report.md": report,
            "date_coverage.csv": _csv_text(coverage),
            "daily_metrics.csv": _csv_text(daily),
            "weekly_aggregate.csv": _csv_text(aggregate),
            "run_manifest.json": json.dumps(
                {
                    **material,
                    "run_hash": run_hash,
                    "captures": captures,
                    "artifact_format": "MARKDOWN_AND_CSV",
                    "xlsx_status": "ARTIFACT_TOOL_RUNTIME_UNAVAILABLE",
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        }.items():
            path = target / filename
            write_immutable_text(path, content)
            paths[filename] = str(path.resolve())
        print(
            json.dumps(
                {
                    "status": "INSUFFICIENT_DATA",
                    "run_hash": run_hash,
                    "recommendation_dates": [value.isoformat() for value in valid_dates],
                    "cutoff": args.cutoff,
                    "daily_rows": len(daily),
                    "aggregate_rows": len(aggregate),
                    "artifacts": paths,
                    "external_api_calls": 0,
                    "llm_calls": 0,
                    "search_calls": 0,
                    "orders": 0,
                    "scheduler": False,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
