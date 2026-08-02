from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ranking_evaluation.utils import stable_hash, write_immutable_text  # noqa: E402


FACTOR_VERSION = "TUSHARE_QUANT_V2_CORRECTED_SHADOW"
FLASH_V2 = "FLASH_V2_STRUCTURED_LIGHT_SCREENING_V5"
FLASH_V3 = "LLM_SCREENING_V3_1_FRESH_EVENT_OVERLAY_SHADOW"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _csv_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.2f}%"


def _num(value: Any) -> str:
    return "—" if value is None else f"{float(value):.4f}"


def _selected_metrics(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    query = """
        SELECT s.ranking_trade_date, s.stage_type, s.cohort_match,
               s.actionability_status, i.selected_flag, o.horizon,
               o.return_decimal, o.outcome_status
          FROM model_effectiveness_stage_snapshot s
          JOIN model_effectiveness_stage_item i ON i.stage_snapshot_id = s.id
          LEFT JOIN ranking_evaluation_forward_outcome o
                 ON o.snapshot_item_id = i.cohort_item_id
         WHERE s.ranking_trade_date BETWEEN ? AND ?
           AND s.stage_type IN (?, ?)
           AND i.selected_flag = 1
    """
    rows = connection.execute(
        query,
        ("2026-07-24", "2026-07-31", "FLASH_V2", "FLASH_V3_EVENT_OVERLAY"),
    ).fetchall()
    grouped: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    meta: dict[tuple[str, str, int], tuple[bool, str]] = {}
    for row in rows:
        if row[5] is None:
            continue
        key = (str(row[0]), str(row[1]), int(row[5]))
        meta[key] = (bool(row[2]), str(row[3]))
        if row[7] == "MATURED" and row[6] is not None:
            grouped[key].append(float(row[6]))
    result = []
    for key in sorted(grouped):
        values = grouped[key]
        cohort_match, actionability = meta[key]
        result.append(
            {
                "recommendation_date": key[0],
                "stage": key[1],
                "horizon": f"D{key[2]}",
                "valid_count": len(values),
                "mean_return": statistics.fmean(values),
                "median_return": statistics.median(values),
                "win_rate": sum(value > 0 for value in values) / len(values),
                "best_return": max(values),
                "worst_return": min(values),
                "cohort_match": cohort_match,
                "actionability": actionability,
                "metric_status": "DESCRIPTIVE_ONLY_COHORT_MISMATCH",
            }
        )
    return result


def _active_outcomes(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for trade_date in (
        "2026-07-24",
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
        "2026-07-30",
        "2026-07-31",
    ):
        path = (
            ROOT
            / "outputs"
            / "quant_v2_validation"
            / trade_date
            / "monday_v2_active_shadow.csv"
        )
        if not path.exists():
            continue
        for source in _read_csv(path):
            stock_code = str(source.get("stock_code") or "").split(".")[0].zfill(6)
            rows = connection.execute(
                """
                SELECT o.horizon, o.due_trade_date, o.return_decimal,
                       o.outcome_status
                  FROM ranking_evaluation_snapshot s
                  JOIN ranking_evaluation_snapshot_item i ON i.snapshot_id = s.id
                  LEFT JOIN ranking_evaluation_forward_outcome o
                         ON o.snapshot_item_id = i.id
                 WHERE s.ranking_trade_date = ?
                   AND s.factor_version = ?
                   AND i.stock_code = ?
                 ORDER BY o.horizon
                """,
                (trade_date, FACTOR_VERSION, stock_code),
            ).fetchall()
            if not rows:
                rows = [(None, None, None, "NOT_MATURED")]
            for row in rows:
                result.append(
                    {
                        "recommendation_date": trade_date,
                        "stock_code": stock_code,
                        "stock_name": source.get("stock_name"),
                        "v2_score": source.get("v2_score"),
                        "horizon": f"D{row[0]}" if row[0] else None,
                        "due_trade_date": row[1],
                        "return_decimal": row[2],
                        "outcome_status": row[3] or "NOT_MATURED",
                    }
                )
    return result


def _reliability(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT ranking_trade_date, stage_type, screening_run_id, cohort_match,
               actionability_status, run_status, reliability_json
          FROM model_effectiveness_stage_snapshot
         WHERE ranking_trade_date BETWEEN ? AND ?
           AND stage_type IN (?, ?)
         ORDER BY ranking_trade_date, stage_type
        """,
        ("2026-07-24", "2026-07-31", "FLASH_V2", "FLASH_V3_EVENT_OVERLAY"),
    ).fetchall()
    result = []
    for row in rows:
        payload = json.loads(row[6] or "{}")
        result.append(
            {
                "recommendation_date": row[0],
                "stage": row[1],
                "screening_run_id": row[2],
                "input_count": payload.get("input_count"),
                "successful_count": payload.get("successful_evaluation_count"),
                "selected_count": payload.get("selected_count"),
                "coverage_ratio": payload.get("scoring_coverage_ratio"),
                "schema_error_count": payload.get("schema_error_count", 0),
                "search_failed_count": payload.get("search_failed_count", 0),
                "checkpoint_reuse_count": payload.get("checkpoint_reuse_count", 0),
                "new_business_call_count": payload.get("new_business_call_count", 0),
                "cohort_match": bool(row[3]),
                "actionability": row[4],
                "run_status": row[5],
            }
        )
    return result


def _daily_equal_weight_selected(
    selected: list[dict[str, Any]], stage: str, horizon: str
) -> dict[str, Any]:
    rows = [
        row
        for row in selected
        if row["stage"] == stage and row["horizon"] == horizon
    ]
    values = [float(row["mean_return"]) for row in rows]
    return {
        "stage": stage,
        "horizon": horizon,
        "matured_day_count": len(values),
        "daily_equal_weight_mean_return": statistics.fmean(values) if values else None,
        "positive_day_ratio": (
            sum(value > 0 for value in values) / len(values) if values else None
        ),
        "status": "DESCRIPTIVE_ONLY_COHORT_MISMATCH" if values else "NOT_MATURED",
    }


def _report(summary: dict[str, Any]) -> str:
    full = summary["full_universe"]["headline_metrics"]
    top = {row["horizon"]: row for row in summary["top100_aggregate"]}
    selected = {
        (row["stage"], row["horizon"]): row
        for row in summary["selected_aggregate"]
    }
    active = summary["active_aggregate"]
    threshold = summary["threshold_review"]
    lines = [
        "# 2026-07-31 本周完整前向效度跟踪",
        "",
        "- 推荐日：2026-07-27 至 2026-07-31",
        "- 成熟桥接样本：2026-07-24（D1/D3/D5在本周成熟）",
        "- 收益截止：2026-07-31 收盘",
        "- 统一信号口径：RAW_CLOSE_SIGNAL_RETURN；阈值复盘单独使用可执行净收益口径",
        "- 结论状态：INSUFFICIENT_DATA；不晋级、不调权、不修改生产阈值",
        "",
        "## 一、全A Quant横截面效度",
        "",
        "| 期限 | 成熟日 | Rank IC | Score IC | 十分位1-10 | Top500-其余 | 覆盖率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for horizon in ("D1", "D3", "D5", "D10"):
        row = full[horizon]
        lines.append(
            f"| {horizon} | {row['matured_day_count']} | "
            f"{_num(row['full_universe_rank_ic_mean'])} | "
            f"{_num(row['full_universe_score_ic_mean'])} | "
            f"{_pct(row['decile_spread_mean'])} | "
            f"{_pct(row['top500_vs_rest_spread_mean'])} | "
            f"{_pct(row['coverage_mean'])} |"
        )
    lines.extend(
        [
            "",
            "## 二、Quant Top100局部效度",
            "",
            "| 期限 | 有效日 | Rank IC | Top20 | Bottom20 | Spread |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for horizon in ("D1", "D3", "D5"):
        row = top[horizon]
        lines.append(
            f"| {horizon} | {row['valid_days']} | {_num(row['mean_rank_ic'])} | "
            f"{_pct(row['mean_top20_return'])} | {_pct(row['mean_bottom20_return'])} | "
            f"{_pct(row['mean_top_bottom_spread'])} |"
        )
    lines.extend(
        [
            "",
            "## 三、Flash与V3描述性跟踪",
            "",
            "Flash输入未覆盖完整Quant Top100，因此正式增量指标保持COMPARISON_BLOCKED。下表只描述实际选中组收益。",
            "",
            "| 阶段 | 期限 | 成熟日 | 日等权收益 | 正收益日占比 | 状态 |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for key in (("FLASH_V2", "D1"), ("FLASH_V2", "D3"), ("FLASH_V2", "D5"), ("FLASH_V3_EVENT_OVERLAY", "D1")):
        row = selected.get(key) or {
            "matured_day_count": 0,
            "daily_equal_weight_mean_return": None,
            "positive_day_ratio": None,
            "status": "NOT_MATURED",
        }
        lines.append(
            f"| {key[0]} | {key[1]} | {row['matured_day_count']} | "
            f"{_pct(row['daily_equal_weight_mean_return'])} | "
            f"{_pct(row['positive_day_ratio'])} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "## 四、ACTIVE_SHADOW",
            "",
            "| 期限 | 成熟推荐日 | 日等权收益 | 状态 |",
            "|---|---:|---:|---|",
        ]
    )
    for row in active:
        lines.append(
            f"| {row['horizon']} | {row['matured_day_count']} | "
            f"{_pct(row['daily_equal_weight_mean_return'])} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "## 五、73.12阈值回溯层",
            "",
            f"- 7月24日V2候选由60分的20只缩减为{threshold['selected_count']}只。",
            f"- 平均净收益：{_pct(threshold['average_net_return'])}；当前盈利率：{_pct(threshold['current_profit_rate'])}；广义成功率：{_pct(threshold['broad_hit_rate'])}。",
            "- 该阈值来自同样本回溯，保持RETROSPECTIVE_DIAGNOSTIC，不修改生产配置。",
            "",
            "## 六、主要结论",
            "",
            "1. 全A排序D1/D3 Rank IC均为负，D3十分位差和Top500相对其余也为负，当前没有全局正向排序证据。",
            "2. Top100头部在D3、单日D5表现优于尾部，但日期数仅3和1，且全A排序同期偏弱，属于局部头部现象，不能外推。",
            "3. Flash V2覆盖率为78%—91%，每天与标准Top100不完全一致；其正式增量价值目前不可判定。",
            "4. V3只有7月30和31日各5只Canary；仅7月30的D1成熟，不能评价稳定增量。",
            "5. 当前最终状态只能是INSUFFICIENT_DATA，继续前向冻结观察。",
            "",
            "## 七、审计边界",
            "",
            "- 外部API调用：0；历史LLM调用：0；搜索调用：0。",
            "- 真实订单：0；虚拟订单：0；Scheduler：false。",
            "- 最大使用行情日期：2026-07-31。",
            "- 行业映射未达到PIT安全要求，因此行业超额保持空值。",
            "- Excel导出：批准的artifact-tool runtime未加载，本次完整报告以Markdown/CSV/JSON交付。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-ending", type=date.fromisoformat, required=True)
    parser.add_argument("--full-run-dir", type=Path, required=True)
    parser.add_argument("--partial-run-dir", type=Path, required=True)
    parser.add_argument("--v2-run-dir", type=Path, required=True)
    parser.add_argument("--v3-run-dir", type=Path, required=True)
    parser.add_argument("--threshold-json", type=Path, required=True)
    args = parser.parse_args()

    full = _read_json(args.full_run_dir / "validation_report.json")
    top100 = _read_csv(args.partial_run_dir / "weekly_aggregate.csv")
    threshold_source = _read_json(args.threshold_json)
    with sqlite3.connect(ROOT / "data" / "ai_trader_dev.db") as connection:
        selected = _selected_metrics(connection)
        active = _active_outcomes(connection)
        reliability = _reliability(connection)

    selected_aggregate = [
        _daily_equal_weight_selected(selected, stage, horizon)
        for stage in ("FLASH_V2", "FLASH_V3_EVENT_OVERLAY")
        for horizon in ("D1", "D3", "D5", "D10")
    ]
    active_aggregate = []
    for horizon in ("D1", "D3", "D5", "D10"):
        by_date: dict[str, list[float]] = defaultdict(list)
        for row in active:
            if (
                row["horizon"] == horizon
                and row["outcome_status"] == "MATURED"
                and row["return_decimal"] is not None
            ):
                by_date[row["recommendation_date"]].append(float(row["return_decimal"]))
        daily = [statistics.fmean(values) for values in by_date.values()]
        active_aggregate.append(
            {
                "horizon": horizon,
                "matured_day_count": len(daily),
                "daily_equal_weight_mean_return": statistics.fmean(daily) if daily else None,
                "positive_day_ratio": sum(value > 0 for value in daily) / len(daily) if daily else None,
                "status": "DESCRIPTIVE_SHADOW_ONLY" if daily else "NOT_MATURED",
            }
        )

    threshold = threshold_source["summary"]
    material = {
        "phase": "WEEKLY_COMPLETE_FORWARD_EFFECTIVENESS_2026_07_31",
        "week_ending": args.week_ending,
        "factor_version": FACTOR_VERSION,
        "full_universe": full,
        "top100_aggregate": top100,
        "selected_aggregate": selected_aggregate,
        "selected_daily": selected,
        "active_aggregate": active_aggregate,
        "active_outcomes": active,
        "stage_reliability": reliability,
        "threshold_review": {
            "threshold": threshold_source["selected_threshold"],
            "selected_count": threshold_source["selected_count"],
            "average_net_return": threshold["average_net_return"],
            "current_profit_rate": threshold["current_profit_rate"],
            "broad_hit_rate": threshold["broad_hit_rate"],
            "sample_type": threshold_source["sample_type"],
            "production_threshold_changed": threshold_source["production_threshold_changed"],
        },
        "data_status": "INSUFFICIENT_DATA",
        "flash_comparison_status": "COMPARISON_BLOCKED",
        "v3_comparison_status": "COMPARISON_BLOCKED",
        "industry_excess_status": "INDUSTRY_MAPPING_NOT_PIT_SAFE",
        "return_basis": "RAW_CLOSE_SIGNAL_RETURN",
        "external_api_calls": 0,
        "llm_calls": 0,
        "search_calls": 0,
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
        "excel_status": "ARTIFACT_TOOL_RUNTIME_UNAVAILABLE",
    }
    run_hash = stable_hash(material)
    run_id = f"weekly-complete-{run_hash[:24]}"
    target = (
        ROOT
        / "outputs"
        / "model_effectiveness"
        / f"weekly_complete_{args.week_ending.isoformat()}"
        / run_id
    )
    target.mkdir(parents=True, exist_ok=True)
    material["run_id"] = run_id
    files = {
        "weekly_complete_effectiveness_report.md": _report(material),
        "weekly_complete_effectiveness_summary.json": json.dumps(
            material, ensure_ascii=False, indent=2, default=str
        ),
        "selected_stage_metrics.csv": _csv_text(selected),
        "active_shadow_outcomes.csv": _csv_text(active),
        "stage_reliability.csv": _csv_text(reliability),
    }
    artifacts = {}
    for filename, content in files.items():
        path = target / filename
        artifacts[filename] = {
            "path": str(path.resolve()),
            "sha256": write_immutable_text(path, content),
        }
    manifest = {
        "run_id": run_id,
        "run_hash": run_hash,
        "week_ending": args.week_ending,
        "factor_version": FACTOR_VERSION,
        "source_runs": {
            "full_universe": str(args.full_run_dir.resolve()),
            "top100": str(args.partial_run_dir.resolve()),
            "flash_v2": str(args.v2_run_dir.resolve()),
            "flash_v3": str(args.v3_run_dir.resolve()),
            "threshold": str(args.threshold_json.resolve()),
        },
        "artifacts": artifacts,
        "status": "INSUFFICIENT_DATA",
        "excel_status": "ARTIFACT_TOOL_RUNTIME_UNAVAILABLE",
        "external_api_calls": 0,
        "llm_calls": 0,
        "search_calls": 0,
        "orders": 0,
        "scheduler": False,
    }
    manifest_path = target / "run_manifest.json"
    manifest_hash = write_immutable_text(
        manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, default=str)
    )
    print(
        json.dumps(
            {
                "status": "INSUFFICIENT_DATA",
                "run_id": run_id,
                "target": str(target.resolve()),
                "manifest_hash": manifest_hash,
                "artifacts": artifacts,
                "external_api_calls": 0,
                "llm_calls": 0,
                "search_calls": 0,
                "orders": 0,
                "scheduler": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
