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


PERIOD = "20250331"
ENDPOINTS = {
    "stock_basic": {"exchange": "", "list_status": "L"},
    "stock_company": {"exchange": "SSE"},
    "fina_mainbz_vip": {"period": PERIOD, "type": "P"},
    "income_vip": {"period": PERIOD},
    "balancesheet_vip": {"period": PERIOD},
    "cashflow_vip": {"period": PERIOD},
    "fina_indicator_vip": {"period": PERIOD},
    "forecast": {"period": PERIOD},
    "express": {"period": PERIOD},
    "fina_audit": {"period": PERIOD},
    "disclosure_date": {"end_date": PERIOD},
    "dividend": {"end_date": PERIOD},
}


def probe() -> list[dict]:
    provider = TushareMarketDataProvider(max_retry=1, request_interval_seconds=0.05)
    configured = provider.token_configured()
    rows: list[dict] = []
    for endpoint, params in ENDPOINTS.items():
        started = time.perf_counter()
        result = provider.query_endpoint(endpoint, params=params, limit=1, use_cache=False, write_cache=False)
        rows.append(
            {
                "endpoint": endpoint,
                "configured": "CONFIGURED" if configured else "NOT_CONFIGURED",
                "authorized": result.status in {"available", "empty", "field_mismatch"},
                "test_status": result.status.upper(),
                "row_count": int(result.raw_data.get("row_count", len(result.records))),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error_category": result.error_type,
            }
        )
    return rows


if __name__ == "__main__":
    print(json.dumps({"capabilities": probe()}, ensure_ascii=False, indent=2))
