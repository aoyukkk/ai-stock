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

from scripts.build_weekly_recommendation_review import (
    EVALUATION_DATE,
    ROOT,
    _canonical_hash,
    build_simple_payload,
)


OUTPUT_DIR = ROOT / "outputs" / "weekly_review"
OUTPUT_PATH = OUTPUT_DIR / f"今日推荐板块成功率_{EVALUATION_DATE}.xlsx"
BUILDER_PATH = ROOT / "scripts" / "build_today_sector_success_rate.mjs"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mean(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return statistics.fmean(values) if values else None


def _sample_status(count: int) -> str:
    if count == 0:
        return "暂无成熟样本"
    if count < 5:
        return "样本极少"
    if count < 10:
        return "样本较少"
    return "可初步观察"


def build_payload(connection: sqlite3.Connection) -> dict[str, Any]:
    weekly, _ = build_simple_payload(connection)
    today_rows = [
        row
        for row in weekly["formal_rows"]
        if row["recommendation_date"] == EVALUATION_DATE
    ]
    minimum_score = float(weekly["minimum_recommendation_score"])
    if not today_rows or any(float(row["pro_score"]) < minimum_score for row in today_rows):
        raise RuntimeError("TODAY_RECOMMENDATION_THRESHOLD_CONTRACT_INVALID")

    today_by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in today_rows:
        today_by_sector[str(row.get("industry") or "未知行业")].append(row)

    history_rows = [
        row
        for row in weekly["overview_rows"]
        if row["first_recommendation_date"] < EVALUATION_DATE
        and row["eligible"]
        and str(row.get("industry") or "未知行业") in today_by_sector
    ]
    history_by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history_rows:
        history_by_sector[str(row.get("industry") or "未知行业")].append(row)

    sector_rows: list[dict[str, Any]] = []
    for sector, current in today_by_sector.items():
        history = history_by_sector.get(sector, [])
        classes = Counter(str(row["result_class"]) for row in history)
        mature_count = len(history)
        strong = classes["STRONG_SUCCESS"]
        stable = classes["STABLE_SUCCESS"]
        giveback = classes["OPPORTUNITY_HIT_GIVEBACK"]
        fail = classes["FAIL"]
        sector_rows.append(
            {
                "sector": sector,
                "today_count": len(current),
                "today_stocks": "、".join(
                    f"{str(row['stock_code']).split('.')[0]} {row['stock_name']}"
                    for row in sorted(
                        current, key=lambda item: int(item.get("pro_rank") or 999999)
                    )
                ),
                "mature_count": mature_count,
                "strong_success": strong,
                "stable_success": stable,
                "opportunity_hit_giveback": giveback,
                "fail": fail,
                "current_profit_rate": (
                    (strong + stable) / mature_count if mature_count else None
                ),
                "opportunity_hit_rate": (
                    (strong + stable + giveback) / mature_count
                    if mature_count
                    else None
                ),
                "average_net_return": _mean(history, "current_net_return"),
                "average_mfe": _mean(history, "mfe"),
                "sample_status": _sample_status(mature_count),
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

    sector_lookup = {row["sector"]: row for row in sector_rows}
    today_detail = []
    for row in sorted(today_rows, key=lambda item: int(item.get("pro_rank") or 999999)):
        sector = str(row.get("industry") or "未知行业")
        stats = sector_lookup[sector]
        today_detail.append(
            {
                **row,
                "sector": sector,
                "sector_mature_count": stats["mature_count"],
                "sector_current_profit_rate": stats["current_profit_rate"],
                "sector_opportunity_hit_rate": stats["opportunity_hit_rate"],
                "sector_sample_status": stats["sample_status"],
            }
        )

    aggregate_classes = Counter(str(row["result_class"]) for row in history_rows)
    mature_total = len(history_rows)
    strong_total = aggregate_classes["STRONG_SUCCESS"]
    stable_total = aggregate_classes["STABLE_SUCCESS"]
    giveback_total = aggregate_classes["OPPORTUNITY_HIT_GIVEBACK"]
    summary = {
        "today_recommendation_count": len(today_rows),
        "today_sector_count": len(sector_rows),
        "historical_mature_unique_count": mature_total,
        "strong_success": strong_total,
        "stable_success": stable_total,
        "opportunity_hit_giveback": giveback_total,
        "fail": aggregate_classes["FAIL"],
        "current_profit_rate": (
            (strong_total + stable_total) / mature_total if mature_total else None
        ),
        "opportunity_hit_rate": (
            (strong_total + stable_total + giveback_total) / mature_total
            if mature_total
            else None
        ),
        "average_net_return": _mean(history_rows, "current_net_return"),
        "average_mfe": _mean(history_rows, "mfe"),
    }
    payload = {
        "phase": "Today Recommendation Sector Success Rate",
        "trade_date": EVALUATION_DATE,
        "baseline_version": weekly["baseline_version"],
        "minimum_recommendation_score": minimum_score,
        "recommendation_score_field": "pro_score",
        "summary": summary,
        "sector_rows": sector_rows,
        "today_rows": today_detail,
        "history_rows": sorted(
            history_rows,
            key=lambda row: (
                str(row.get("industry") or "未知行业"),
                str(row["first_recommendation_date"]),
                str(row["stock_code"]),
            ),
        ),
        "class_rows": [
            {"label": "强成功", "count": strong_total},
            {"label": "稳定成功", "count": stable_total},
            {"label": "机会命中后回吐", "count": giveback_total},
            {"label": "失败", "count": aggregate_classes["FAIL"]},
        ],
        "audit": {
            "today_recommendation_count_expected": len(today_rows),
            "today_sector_count": len(sector_rows),
            "historical_rows": mature_total,
            "sheet_count_expected": 4,
            "chart_count_expected": 2,
            "formula_error_expected": 0,
            "stock_deduplication": "WEEKLY_OVERVIEW_FIRST_LEGAL_ENTRY",
            "today_pending_excluded": True,
            "real_orders": 0,
            "virtual_orders": 0,
            "external_api_calls": 0,
            "llm_calls": 0,
        },
    }
    payload["input_hash"] = _canonical_hash(
        {
            "version": "TODAY_SECTOR_SUCCESS_RATE_V1",
            "today_rows": today_detail,
            "history_rows": payload["history_rows"],
        }
    )
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def _build_workbook(
    payload: dict[str, Any], output_path: Path, preview_dir: Path
) -> dict[str, Any]:
    build_dir = output_path.parent / f".sector-build-{uuid.uuid4().hex}"
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
                f"SECTOR_WORKBOOK_EXPORT_FAILED:{completed.returncode}:"
                f"{completed.stderr[-1500:]}"
            )
        if not qa_path.is_file():
            raise RuntimeError("SECTOR_WORKBOOK_QA_MISSING")
        return json.loads(qa_path.read_text(encoding="utf-8"))
    finally:
        if link.exists():
            os.rmdir(link)
        shutil.rmtree(build_dir, ignore_errors=True)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(ROOT / "data" / "ai_trader_dev.db")
    candidate = OUTPUT_DIR / f".today-sector-{uuid.uuid4().hex[:10]}.xlsx"
    try:
        payload = build_payload(connection)
    finally:
        connection.close()
    qa = _build_workbook(
        payload,
        candidate,
        OUTPUT_DIR / f"preview_today_sector_{EVALUATION_DATE}",
    )
    if (
        qa.get("sheet_count") != payload["audit"]["sheet_count_expected"]
        or qa.get("chart_count") != payload["audit"]["chart_count_expected"]
        or qa.get("formula_error_count") != 0
    ):
        raise RuntimeError(f"SECTOR_WORKBOOK_QA_FAILED:{qa}")
    candidate_hash = _sha256_file(candidate)
    if OUTPUT_PATH.exists():
        if _sha256_file(OUTPUT_PATH) != candidate_hash:
            raise RuntimeError("IMMUTABLE_SECTOR_WORKBOOK_CONFLICT")
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
    (OUTPUT_DIR / f"today-sector-{payload['input_hash'][:16]}_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
