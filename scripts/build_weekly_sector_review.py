from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_today_sector_success_rate import _sample_status
from scripts.build_weekly_recommendation_review import (
    END_DATE,
    ROOT,
    START_DATE,
    TRADE_DATES,
    _canonical_hash,
    build_simple_payload,
)


OUTPUT_DIR = ROOT / "outputs" / "weekly_review"
OUTPUT_PATH = OUTPUT_DIR / f"整周每日推荐板块复盘_{START_DATE}至{END_DATE}.xlsx"
BUILDER_PATH = ROOT / "scripts" / "build_weekly_sector_review.mjs"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mean(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return statistics.fmean(values) if values else None


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mature = [row for row in rows if row.get("eligible")]
    classes = Counter(str(row["result_class"]) for row in mature)
    count = len(mature)
    strong = classes["STRONG_SUCCESS"]
    stable = classes["STABLE_SUCCESS"]
    giveback = classes["OPPORTUNITY_HIT_GIVEBACK"]
    return {
        "mature_count": count,
        "strong_success": strong,
        "stable_success": stable,
        "opportunity_hit_giveback": giveback,
        "fail": classes["FAIL"],
        "current_profit_rate": (strong + stable) / count if count else None,
        "opportunity_hit_rate": (
            (strong + stable + giveback) / count if count else None
        ),
        "average_net_return": _mean(mature, "current_net_return"),
        "average_mfe": _mean(mature, "mfe"),
        "pending_or_excluded": len(rows) - count,
        "sample_status": _sample_status(count),
    }


def build_payload(connection: sqlite3.Connection) -> dict[str, Any]:
    weekly, _ = build_simple_payload(connection)
    formal = weekly["formal_rows"]
    overview = weekly["overview_rows"]

    formal_by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    overview_by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in formal:
        formal_by_sector[str(row.get("industry") or "未知行业")].append(row)
    for row in overview:
        overview_by_sector[str(row.get("industry") or "未知行业")].append(row)

    sector_rows = []
    for sector, recommendation_rows in formal_by_sector.items():
        unique_rows = overview_by_sector[sector]
        sector_rows.append(
            {
                "sector": sector,
                "recommendation_count": len(recommendation_rows),
                "unique_stock_count": len(unique_rows),
                "recommendation_dates": "、".join(
                    sorted({str(row["recommendation_date"]) for row in recommendation_rows})
                ),
                **_stats(unique_rows),
            }
        )
    sector_rows.sort(
        key=lambda row: (
            row["mature_count"] == 0,
            -(row["current_profit_rate"] or 0),
            -(row["opportunity_hit_rate"] or 0),
            -row["mature_count"],
            row["sector"],
        )
    )

    daily_sector_rows = []
    for trade_date in TRADE_DATES:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in formal:
            if row["recommendation_date"] == trade_date:
                grouped[str(row.get("industry") or "未知行业")].append(row)
        for sector, rows in grouped.items():
            stats = _stats(rows)
            daily_sector_rows.append(
                {
                    "recommendation_date": trade_date,
                    "sector": sector,
                    "recommendation_count": len(rows),
                    "stocks": "、".join(
                        f"{str(row['stock_code']).split('.')[0]} {row['stock_name']}"
                        for row in sorted(
                            rows, key=lambda item: int(item.get("pro_rank") or 999999)
                        )
                    ),
                    **stats,
                }
            )
    daily_sector_rows.sort(
        key=lambda row: (
            row["recommendation_date"],
            row["mature_count"] == 0,
            -(row["current_profit_rate"] or 0),
            row["sector"],
        )
    )

    summary_stats = _stats(overview)
    summary = {
        "recommendation_count": len(formal),
        "unique_stock_count": len(overview),
        "sector_count": len(sector_rows),
        **summary_stats,
    }
    class_rows = [
        {"label": "强成功", "count": summary["strong_success"]},
        {"label": "稳定成功", "count": summary["stable_success"]},
        {"label": "机会命中后回吐", "count": summary["opportunity_hit_giveback"]},
        {"label": "失败", "count": summary["fail"]},
    ]
    payload = {
        "phase": "Weekly Daily Recommendation Sector Review",
        "start_date": START_DATE,
        "end_date": END_DATE,
        "trade_dates": list(TRADE_DATES),
        "minimum_recommendation_score": weekly["minimum_recommendation_score"],
        "recommendation_score_field": "pro_score",
        "summary": summary,
        "sector_rows": sector_rows,
        "daily_sector_rows": daily_sector_rows,
        "recommendation_rows": sorted(
            formal,
            key=lambda row: (
                str(row["recommendation_date"]),
                int(row.get("pro_rank") or 999999),
            ),
        ),
        "overview_rows": overview,
        "class_rows": class_rows,
        "chart_sector_rows": [
            row
            for row in sector_rows
            if row["mature_count"] >= 2
        ][:15],
        "audit": {
            "recommendation_count_expected": len(formal),
            "unique_stock_count": len(overview),
            "sector_count": len(sector_rows),
            "mature_count": summary["mature_count"],
            "sheet_count_expected": 5,
            "chart_count_expected": 2,
            "formula_error_expected": 0,
            "stock_deduplication": "WEEKLY_OVERVIEW_FIRST_LEGAL_ENTRY",
            "pending_excluded_from_rates": True,
            "real_orders": 0,
            "virtual_orders": 0,
            "external_api_calls": 0,
            "llm_calls": 0,
        },
    }
    payload["input_hash"] = _canonical_hash(
        {
            "version": "WEEKLY_DAILY_SECTOR_REVIEW_V1",
            "sector_rows": sector_rows,
            "daily_sector_rows": daily_sector_rows,
            "overview_rows": overview,
        }
    )
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def _build_workbook(
    payload: dict[str, Any], output_path: Path, preview_dir: Path
) -> dict[str, Any]:
    build_dir = output_path.parent / f".weekly-sector-build-{uuid.uuid4().hex}"
    build_dir.mkdir(parents=True, exist_ok=False)
    link = build_dir / "node_modules"
    try:
        builder = build_dir / BUILDER_PATH.name
        shutil.copy2(BUILDER_PATH, builder)
        payload_path = build_dir / "payload.json"
        qa_path = build_dir / "qa.json"
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
        )
        dependency_root = (
            Path.home()
            / ".cache"
            / "codex-runtimes"
            / "codex-primary-runtime"
            / "dependencies"
            / "node"
            / "node_modules"
        )
        if not (dependency_root / "@oai" / "artifact-tool").exists():
            raise RuntimeError("ARTIFACT_TOOL_DEPENDENCY_NOT_FOUND")
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(dependency_root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if junction.returncode != 0:
            raise RuntimeError(f"ARTIFACT_TOOL_JUNCTION_FAILED:{junction.stderr}")
        completed = subprocess.run(
            [
                "node",
                str(builder),
                str(payload_path),
                str(output_path),
                str(preview_dir),
                str(qa_path),
            ],
            cwd=build_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if not output_path.is_file():
            raise RuntimeError(
                f"WEEKLY_SECTOR_EXPORT_FAILED:{completed.returncode}:"
                f"{completed.stderr[-1500:]}"
            )
        if not qa_path.is_file():
            raise RuntimeError("WEEKLY_SECTOR_QA_MISSING")
        return json.loads(qa_path.read_text(encoding="utf-8"))
    finally:
        if link.exists():
            os.rmdir(link)
        shutil.rmtree(build_dir, ignore_errors=True)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(ROOT / "data" / "ai_trader_dev.db")
    candidate = OUTPUT_DIR / f".weekly-sector-{uuid.uuid4().hex[:10]}.xlsx"
    try:
        payload = build_payload(connection)
    finally:
        connection.close()
    qa = _build_workbook(
        payload,
        candidate,
        OUTPUT_DIR / f"preview_weekly_sector_{START_DATE}_{END_DATE}",
    )
    if (
        qa.get("sheet_count") != payload["audit"]["sheet_count_expected"]
        or qa.get("chart_count") != payload["audit"]["chart_count_expected"]
        or qa.get("formula_error_count") != 0
    ):
        raise RuntimeError(f"WEEKLY_SECTOR_QA_FAILED:{qa}")
    candidate_hash = _sha256_file(candidate)
    if OUTPUT_PATH.exists():
        if _sha256_file(OUTPUT_PATH) != candidate_hash:
            raise RuntimeError("IMMUTABLE_WEEKLY_SECTOR_CONFLICT")
        candidate.unlink(missing_ok=True)
    else:
        candidate.replace(OUTPUT_PATH)
    report = {
        "status": "COMPLETED",
        "workbook": str(OUTPUT_PATH.resolve()),
        "workbook_hash": _sha256_file(OUTPUT_PATH),
        "summary": payload["summary"],
        "qa": qa,
        "input_hash": payload["input_hash"],
    }
    (OUTPUT_DIR / f"weekly-sector-{payload['input_hash'][:16]}_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
