from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from datasource.tushare_provider import TushareMarketDataProvider
from scripts.probe_tushare_permissions import PROBE_ENDPOINTS


REPORT_PATH = Path("data/reports/tushare_data_source_smoke_report.json")


def run_smoke(limit: int = 5, output: str | Path = REPORT_PATH) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    started_perf = time.perf_counter()
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    provider = TushareMarketDataProvider(cache_enabled=False)

    results: dict[str, dict[str, Any]] = {}
    for spec in PROBE_ENDPOINTS:
        result = provider.query_endpoint(
            spec["api"],
            params=spec.get("params"),
            fields=spec.get("fields"),
            required_fields=spec.get("required"),
            limit=limit if limit > 0 else spec.get("limit", 5),
            use_cache=False,
        )
        results[spec["api"]] = {
            "status": result.status,
            "row_count": len(result.records),
            "sample": result.records[: min(limit, len(result.records))],
            "missing_fields": result.missing_fields,
            "error_type": result.error_type,
            "error_message": result.error_message,
        }

    finished = datetime.now(timezone.utc)
    report = {
        "token_configured": provider.token_configured(),
        "results": results,
        "summary": {
            "available": sorted(api for api, item in results.items() if item["status"] == "available"),
            "permission_denied": sorted(api for api, item in results.items() if item["status"] == "permission_denied"),
            "empty": sorted(api for api, item in results.items() if item["status"] == "empty"),
            "field_mismatch": sorted(api for api, item in results.items() if item["status"] == "field_mismatch"),
            "error": sorted(api for api, item in results.items() if item["status"] in {"error", "not_configured"}),
        },
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round(time.perf_counter() - started_perf, 3),
        "report_path": str(output_path),
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test Tushare manual data source endpoints.")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()

    report = run_smoke(limit=args.limit, output=args.output)
    summary = report["summary"]
    print(
        "summary: "
        f"token_configured={report['token_configured']} "
        f"available={len(summary['available'])} "
        f"permission_denied={len(summary['permission_denied'])} "
        f"empty={len(summary['empty'])} "
        f"field_mismatch={len(summary['field_mismatch'])} "
        f"error={len(summary['error'])} "
        f"report_path={report['report_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
