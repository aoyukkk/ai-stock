from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_weekly_recommendation_review as legacy_review  # noqa: E402
RECOMMENDATION_DATE = "2026-07-24"
EVALUATION_DATE = "2026-07-31"
RETURN_DATES = (
    "2026-07-27",
    "2026-07-28",
    "2026-07-29",
    "2026-07-30",
    "2026-07-31",
)
FACTOR_VERSION = "TUSHARE_QUANT_V2_CORRECTED_SHADOW"
MIN_ELIGIBLE_SAMPLE = 8


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _costs() -> dict[str, float]:
    settings = yaml.safe_load(
        (ROOT / "config" / "paper_trading.yaml").read_text(encoding="utf-8")
    )["paper_trading"]["cost"]
    return {
        "commission_rate": float(settings["commission_rate"]),
        "min_commission": float(settings["min_commission"]),
        "stamp_tax_rate": float(settings["stamp_tax_rate"]),
        "slippage_rate": float(settings["slippage_rate"]),
    }


def _ts_code(row: dict[str, Any]) -> str:
    code = str(row["stock_code"]).split(".")[0].zfill(6)
    exchange = str(row.get("exchange") or "").upper()
    if exchange in {"SH", "SZ", "BJ"}:
        return f"{code}.{exchange}"
    return legacy_review._normalize_ts_code(code)


def _build_all_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = ROOT / "outputs" / "quant_v2_validation" / RECOMMENDATION_DATE
    audit_path = root / "monday_v2_candidate_audit.json"
    quant_path = root / "quant_v2_validation.json"
    audit = _read_json(audit_path)
    quant = _read_json(quant_path)
    if audit.get("base_run_id") != quant.get("run_id"):
        raise RuntimeError("V2_SOURCE_RUN_MISMATCH")
    if audit.get("final_status") != "MONDAY_V2_SHADOW_FINALIZED":
        raise RuntimeError("V2_FINALIZATION_NOT_READY")

    stage = quant["v2_run"]["stages"][FACTOR_VERSION]
    quant_rows = {
        str(row["stock_code"]).split(".")[0].zfill(6): row
        for row in stage["top100"]
    }
    pro_rows = list((audit.get("pro") or {}).get("results") or [])
    if len(pro_rows) != 20:
        raise RuntimeError(f"V2_PRO_COHORT_NOT_20:{len(pro_rows)}")

    plans = {
        legacy_review._normalize_ts_code(row["stock_code"]): row
        for row in audit.get("order_plans") or []
    }
    daily_by_date = {
        trade_date: legacy_review._by_code(
            legacy_review._load_cache("daily", trade_date)
        )
        for trade_date in RETURN_DATES
    }
    limit_by_date = {
        trade_date: legacy_review._by_code(
            legacy_review._load_cache("stk_limit", trade_date)
        )
        for trade_date in RETURN_DATES
    }

    legacy_review.TRADE_DATES = RETURN_DATES
    legacy_review.OVERVIEW_RETURN_DATES = RETURN_DATES
    legacy_review.EVALUATION_DATE = EVALUATION_DATE
    legacy_review.NEXT_TRADE_DATE = {RECOMMENDATION_DATE: RETURN_DATES[0]}

    rows: list[dict[str, Any]] = []
    for pro_row in sorted(pro_rows, key=lambda item: int(item.get("pro_rank") or 999)):
        plain_code = str(pro_row["stock_code"]).split(".")[0].zfill(6)
        quant_row = quant_rows.get(plain_code)
        if not quant_row:
            raise RuntimeError(f"V2_QUANT_ROW_MISSING:{plain_code}")
        code = _ts_code(quant_row)
        evaluation = legacy_review._evaluate(
            recommendation_date=RECOMMENDATION_DATE,
            code=code,
            plan=plans.get(code),
            actual_fill=None,
            daily_by_date=daily_by_date,
            limit_by_date=limit_by_date,
            costs=_costs(),
            pending=False,
        )
        rows.append(
            {
                "source_type": "V2_CORRECTED_SHADOW",
                "recommendation_date": RECOMMENDATION_DATE,
                "stock_code": code,
                "stock_name": pro_row.get("stock_name") or quant_row.get("stock_name") or "",
                "industry": quant_row.get("level_one_sector") or "未知行业",
                "quant_run_id": quant["run_id"],
                "pro_run_id": "monday_v2_candidate_audit",
                "quant_rank": int(pro_row.get("quant_rank") or quant_row.get("rank") or 0),
                "quant_score": float(pro_row.get("quant_score") or quant_row.get("total_score") or 0),
                "pro_rank": int(pro_row.get("pro_rank") or 0),
                "pro_score": float(pro_row["pro_score"]),
                "recommendation_grade": pro_row.get("priority"),
                "final_summary": pro_row.get("final_summary"),
                **evaluation,
            }
        )

    cache_audit = [
        {
            "trade_date": trade_date,
            "daily_count": len(daily_by_date[trade_date]),
            "stk_limit_count": len(limit_by_date[trade_date]),
            "daily_hash": _sha(
                ROOT
                / "data"
                / "cache"
                / "tushare"
                / "trade_date"
                / "daily"
                / f"{trade_date.replace('-', '')}.json"
            ),
        }
        for trade_date in RETURN_DATES
    ]
    source = {
        "audit_path": str(audit_path),
        "audit_sha256": _sha(audit_path),
        "quant_path": str(quant_path),
        "quant_sha256": _sha(quant_path),
        "quant_run_id": quant["run_id"],
        "pro_candidate_count": len(pro_rows),
        "daily_cache_audit": cache_audit,
    }
    return rows, source


def _threshold_scan(rows: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    scans: list[dict[str, Any]] = []
    for threshold in sorted({float(row["pro_score"]) for row in rows}):
        selected = [row for row in rows if float(row["pro_score"]) >= threshold]
        eligible = [row for row in selected if row["eligible"]]
        if not eligible:
            continue
        net_returns = [float(row["current_net_return"]) for row in eligible]
        negative_returns = [abs(value) for value in net_returns if value < 0]
        aggregate = legacy_review._aggregate(selected)
        scans.append(
            {
                "threshold": round(threshold, 2),
                "selected_count": len(selected),
                "eligible_count": len(eligible),
                "average_net_return": statistics.fmean(net_returns),
                "median_net_return": statistics.median(net_returns),
                "average_downside_loss": (
                    statistics.fmean(negative_returns) if negative_returns else 0.0
                ),
                "broad_hit_rate": aggregate["broad_hit_rate"],
                "current_profit_rate": aggregate["current_profit_rate"],
                "failure_rate": aggregate["failure_rate"],
                "average_mfe": aggregate["average_mfe"],
                "average_mae": aggregate["average_mae"],
            }
        )
    eligible_scans = [
        row for row in scans if row["eligible_count"] >= MIN_ELIGIBLE_SAMPLE
    ]
    if not eligible_scans:
        raise RuntimeError("THRESHOLD_SCAN_INSUFFICIENT_ELIGIBLE_SAMPLE")
    best = max(
        eligible_scans,
        key=lambda row: (
            row["average_net_return"],
            row["current_profit_rate"] or 0.0,
            row["broad_hit_rate"] or 0.0,
            row["eligible_count"],
        ),
    )
    return float(best["threshold"]), scans


def build(output_path: Path) -> dict[str, Any]:
    rows, source = _build_all_rows()
    threshold, threshold_scan = _threshold_scan(rows)
    selected = [row for row in rows if float(row["pro_score"]) >= threshold]
    for row in selected:
        row["quant_score_band"] = legacy_review._score_band(row["quant_score"])
    overview_rows = legacy_review._deduplicate_overview(selected)
    summary = legacy_review._aggregate(overview_rows)
    summary["sample_status"] = "INSUFFICIENT_SAMPLE"
    summary["conclusion"] = (
        "门槛来自7月24日V2候选的同样本回溯扫描，仅用于本周复盘，不构成生产晋级。"
    )
    caveat = (
        f"回溯诊断：在至少{MIN_ELIGIBLE_SAMPLE}只成熟样本约束下，以平均税费后收益为主目标，"
        f"选得Pro分门槛{threshold:.2f}。这是同样本最优值，可能过拟合；不得直接写入生产配置。"
    )
    payload: dict[str, Any] = {
        "phase": "2026-07-31 V2 Weekly Threshold Review",
        "review_start_date": RECOMMENDATION_DATE,
        "review_end_date": EVALUATION_DATE,
        "evaluation_date": EVALUATION_DATE,
        "baseline_version": FACTOR_VERSION,
        "minimum_recommendation_score": threshold,
        "recommendation_score_field": "pro_score",
        "caveat": caveat,
        "summary": summary,
        "overview_rows": overview_rows,
        "overview_return_dates": list(RETURN_DATES),
        "date_sheets": [
            {"trade_date": RECOMMENDATION_DATE, "rows": selected}
        ],
        "four_classes": [
            {"key": "STRONG_SUCCESS", "label": "强成功"},
            {"key": "STABLE_SUCCESS", "label": "稳定成功"},
            {"key": "OPPORTUNITY_HIT_GIVEBACK", "label": "机会命中后回吐"},
            {"key": "FAIL", "label": "失败"},
        ],
        "formal_rows": selected,
        "today_rows": [],
        "source_audit": [source],
        "daily_cache_audit": source["daily_cache_audit"],
        "threshold_selection": {
            "selected_threshold": threshold,
            "legacy_threshold": 60.0,
            "minimum_eligible_sample": MIN_ELIGIBLE_SAMPLE,
            "selection_method": "MAX_AVG_NET_RETURN_WITH_MIN_SAMPLE",
            "sample_type": "RETROSPECTIVE_DIAGNOSTIC",
            "scan": threshold_scan,
        },
        "audit": {
            "source_candidate_count": len(rows),
            "selected_count": len(selected),
            "unique_stock_count": len(overview_rows),
            "formula_error_expected": 0,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
            "llm_calls_for_weekly_report": 0,
            "external_api_calls_for_weekly_report": 0,
            "production_threshold_changed": False,
        },
    }
    payload["input_hash"] = legacy_review._canonical_hash(
        {
            "factor_version": FACTOR_VERSION,
            "source": source,
            "threshold_selection": payload["threshold_selection"],
            "selected": selected,
            "costs": _costs(),
        }
    )
    payload["run_id"] = f"weekly-v2-threshold-{payload['input_hash'][:16]}"
    payload["content_hash"] = legacy_review._canonical_hash(
        {
            "summary": summary,
            "overview_rows": overview_rows,
            "threshold": threshold,
        }
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview_dir = output_path.parent / "preview_v2_2026-07-24_2026-07-31"
    qa = legacy_review._build_workbook(payload, output_path, preview_dir)
    if qa.get("status") != "PASS":
        raise RuntimeError(f"WEEKLY_WORKBOOK_QA_FAILED:{qa}")
    report_path = output_path.with_suffix(".json")
    report = {
        "status": "WEEKLY_V2_THRESHOLD_REVIEW_READY",
        "run_id": payload["run_id"],
        "factor_version": FACTOR_VERSION,
        "recommendation_date": RECOMMENDATION_DATE,
        "evaluation_date": EVALUATION_DATE,
        "selected_threshold": threshold,
        "legacy_threshold": 60.0,
        "source_candidate_count": len(rows),
        "selected_count": len(selected),
        "summary": summary,
        "threshold_scan": threshold_scan,
        "workbook": str(output_path),
        "workbook_sha256": _sha(output_path),
        "qa": qa,
        "sample_type": "RETROSPECTIVE_DIAGNOSTIC",
        "production_threshold_changed": False,
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            ROOT
            / "outputs"
            / "weekly_review"
            / "本周V2推荐成功率_2026-07-24至2026-07-31.xlsx"
        ),
    )
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
