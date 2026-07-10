from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any


def compare_reports(a_path: Path, b_path: Path, c_path: Path) -> dict[str, Any]:
    reports = {label: _load(path) for label, path in (("A", a_path), ("B", b_path), ("C", c_path))}
    rows = {label: _row_map(report) for label, report in reports.items()}
    return {
        "scenarios": {
            "A": "RAW_BASELINE + price_limit_risk=false",
            "B": "RAW_BASELINE + price_limit_risk=true",
            "C": "QFQ_POINT_IN_TIME + price_limit_risk=true",
        },
        "run_summary": {label: _run_summary(report) for label, report in reports.items()},
        "a_vs_b": _pair_metrics(rows["A"], rows["B"]),
        "b_vs_c": _pair_metrics(rows["B"], rows["C"]),
        "a_vs_c": _pair_metrics(rows["A"], rows["C"]),
        "top_overlap": {
            "a_vs_b": _overlaps(rows["A"], rows["B"]),
            "b_vs_c": _overlaps(rows["B"], rows["C"]),
            "a_vs_c": _overlaps(rows["A"], rows["C"]),
        },
        "price_basis": {
            label: _counts(row.get("technical_price_basis", "UNKNOWN") for row in values.values())
            for label, values in rows.items()
        },
        "limit_status_counts": {
            label: _counts(row.get("limit_status", "UNKNOWN") for row in values.values())
            for label, values in rows.items()
        },
        "safety": {
            "all_no_llm_call_verified": all(report.get("no_llm_call_verified") is True for report in reports.values()),
            "all_per_stock_api_call_count_zero": all(int(report.get("per_stock_api_call_count", -1)) == 0 for report in reports.values()),
            "same_scored_universe": len({report.get("scored_count") for report in reports.values()}) == 1,
        },
        "source_reports": {label: str(path) for label, path in (("A", a_path), ("B", b_path), ("C", c_path))},
    }


def _pair_metrics(left: dict[str, dict], right: dict[str, dict]) -> dict[str, Any]:
    common = sorted(set(left) & set(right))
    rank_changes = [abs(int(right[code]["rank"]) - int(left[code]["rank"])) for code in common]
    technical_deltas = [float(right[code]["technical_score"]) - float(left[code]["technical_score"]) for code in common]
    risk_deltas = [float(right[code]["risk_score"]) - float(left[code]["risk_score"]) for code in common]
    total_deltas = [float(right[code]["total_score"]) - float(left[code]["total_score"]) for code in common]
    return {
        "common_count": len(common),
        "rank_correlation": round(_pearson([left[c]["rank"] for c in common], [right[c]["rank"] for c in common]), 8),
        "median_absolute_rank_change": float(median(rank_changes)) if rank_changes else None,
        "max_absolute_rank_change": max(rank_changes, default=None),
        "median_technical_score_delta": round(float(median(technical_deltas)), 6) if technical_deltas else None,
        "max_absolute_technical_score_delta": round(max((abs(value) for value in technical_deltas), default=0), 6),
        "median_risk_score_delta": round(float(median(risk_deltas)), 6) if risk_deltas else None,
        "max_absolute_risk_score_delta": round(max((abs(value) for value in risk_deltas), default=0), 6),
        "median_total_score_delta": round(float(median(total_deltas)), 6) if total_deltas else None,
        "max_absolute_total_score_delta": round(max((abs(value) for value in total_deltas), default=0), 6),
        "rank_change_by_limit_status": _rank_change_by_status(left, right, common),
    }


def _overlaps(left: dict[str, dict], right: dict[str, dict]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for size in (20, 100, 500):
        left_top = {code for code, row in left.items() if int(row["rank"]) <= size}
        right_top = {code for code, row in right.items() if int(row["rank"]) <= size}
        count = len(left_top & right_top)
        result[f"top_{size}"] = {"count": count, "ratio": round(count / size, 6)}
    return result


def _rank_change_by_status(left: dict[str, dict], right: dict[str, dict], common: list[str]) -> dict[str, Any]:
    grouped: dict[str, list[int]] = {}
    for code in common:
        status = str(right[code].get("limit_status") or "UNKNOWN")
        grouped.setdefault(status, []).append(int(right[code]["rank"]) - int(left[code]["rank"]))
    return {
        status: {
            "count": len(values),
            "median_signed_rank_change": float(median(values)),
            "median_absolute_rank_change": float(median(abs(value) for value in values)),
        }
        for status, values in sorted(grouped.items())
    }


def _run_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "universe_count": report.get("universe_count"),
        "filtered_count": report.get("filtered_count"),
        "scored_count": report.get("scored_count"),
        "skipped_count": report.get("skipped_count"),
        "failed_count": report.get("failed_count"),
        "top_count": report.get("top_count"),
        "duration_seconds": report.get("duration_seconds"),
        "total_seconds": report.get("performance", {}).get("total_seconds"),
        "no_llm_call_verified": report.get("no_llm_call_verified"),
        "per_stock_api_call_count": report.get("per_stock_api_call_count"),
        "price_adjustment_mode": report.get("price_adjustment_mode"),
        "price_limit_risk_enabled": report.get("price_limit_risk_enabled"),
    }


def _row_map(report: dict[str, Any]) -> dict[str, dict]:
    return {str(row["stock_code"]): row for row in report.get("all_scored_stocks", [])}


def _counts(values) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[str(value)] = result.get(str(value), 0) + 1
    return dict(sorted(result.items()))


def _pearson(left, right) -> float:
    if not left or len(left) != len(right):
        return 0.0
    left_values = [float(value) for value in left]
    right_values = [float(value) for value in right]
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_values, right_values))
    left_scale = sum((value - left_mean) ** 2 for value in left_values) ** 0.5
    right_scale = sum((value - right_mean) ** 2 for value in right_values) ** 0.5
    return numerator / (left_scale * right_scale) if left_scale and right_scale else 0.0


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare three quant A/B calibration reports.")
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument("c", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare_reports(args.a, args.b, args.c)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
