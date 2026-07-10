from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env", override=False)

from datasource.tushare_provider import TushareMarketDataProvider


CASES = (
    ("forecast", "period", {"period": "20250331"}),
    ("forecast", "announcement_range", {"start_date": "20250401", "end_date": "20250531"}),
    ("forecast", "ann_date", {"ann_date": "20250430"}),
    ("fina_audit", "period", {"period": "20241231"}),
    ("fina_audit", "stock", {"ts_code": "000001.SZ"}),
)


def diagnose() -> list[dict]:
    provider = TushareMarketDataProvider(max_retry=1, request_interval_seconds=0.05)
    rows = []
    for endpoint, case, params in CASES:
        started = time.perf_counter()
        result = provider.query_endpoint(endpoint, params=params, limit=1, use_cache=False, write_cache=False)
        category = "EMPTY_RESULT" if result.status == "empty" else "RESPONSE_SCHEMA_ERROR" if result.status == "field_mismatch" else result.error_type or "SUCCESS"
        rows.append({
            "endpoint": endpoint, "case": case,
            "configured": "CONFIGURED" if provider.token_configured() else "NOT_CONFIGURED",
            "status": result.status.upper(), "row_count": len(result.records),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error_category": category, "cache_written": False,
        })
    return rows


if __name__ == "__main__":
    print(json.dumps({"diagnostics": diagnose()}, ensure_ascii=False, indent=2))
