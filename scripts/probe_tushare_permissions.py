from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from datasource.tushare_provider import TushareMarketDataProvider


REPORT_PATH = Path("data/reports/tushare_permission_probe_report.json")
SMOKE_DAY = "20240102"
SMOKE_MONTH = "202401"
SMOKE_STOCK = "000001.SZ"
SMOKE_ETF = "510300.SH"


PROBE_ENDPOINTS: tuple[dict[str, Any], ...] = (
    {"module": "basic", "api": "stock_basic", "params": {"exchange": "", "list_status": "L"}, "fields": "ts_code,symbol,name,industry,list_date", "required": {"ts_code", "name"}},
    {"module": "basic", "api": "trade_cal", "params": {"exchange": "", "start_date": SMOKE_DAY, "end_date": SMOKE_DAY}, "fields": "exchange,cal_date,is_open", "required": {"cal_date", "is_open"}},
    {"module": "quotation", "api": "daily", "params": {"ts_code": SMOKE_STOCK, "start_date": SMOKE_DAY, "end_date": SMOKE_DAY}, "fields": "ts_code,trade_date,open,high,low,close,vol,amount", "required": {"ts_code", "trade_date"}},
    {"module": "quotation", "api": "weekly", "params": {"ts_code": SMOKE_STOCK, "start_date": "20240101", "end_date": "20240131"}, "fields": "ts_code,trade_date,open,high,low,close", "required": {"ts_code", "trade_date"}},
    {"module": "quotation", "api": "monthly", "params": {"ts_code": SMOKE_STOCK, "start_date": "20240101", "end_date": "20240131"}, "fields": "ts_code,trade_date,open,high,low,close", "required": {"ts_code", "trade_date"}},
    {"module": "quotation", "api": "adj_factor", "params": {"ts_code": SMOKE_STOCK, "start_date": SMOKE_DAY, "end_date": SMOKE_DAY}, "fields": "ts_code,trade_date,adj_factor", "required": {"ts_code", "trade_date", "adj_factor"}},
    {"module": "quotation", "api": "daily_basic", "params": {"ts_code": SMOKE_STOCK, "trade_date": SMOKE_DAY}, "fields": "ts_code,trade_date,turnover_rate,volume_ratio,pe,pb", "required": {"ts_code", "trade_date"}},
    {"module": "quotation", "api": "stk_limit", "params": {"ts_code": SMOKE_STOCK, "trade_date": SMOKE_DAY}, "fields": "trade_date,ts_code,up_limit,down_limit", "required": {"ts_code", "trade_date"}},
    {"module": "capital", "api": "moneyflow", "params": {"ts_code": SMOKE_STOCK, "start_date": SMOKE_DAY, "end_date": SMOKE_DAY}, "required": {"ts_code", "trade_date"}},
    {"module": "finance", "api": "fina_indicator", "params": {"ts_code": SMOKE_STOCK}, "limit": 1},
    {"module": "finance", "api": "income", "params": {"ts_code": SMOKE_STOCK}, "limit": 1},
    {"module": "finance", "api": "balancesheet", "params": {"ts_code": SMOKE_STOCK}, "limit": 1},
    {"module": "finance", "api": "cashflow", "params": {"ts_code": SMOKE_STOCK}, "limit": 1},
    {"module": "emotion", "api": "ths_index", "params": {"exchange": "A", "type": "N"}, "required": {"ts_code", "name"}, "limit": 1},
    {"module": "emotion", "api": "ths_member", "params": {"ts_code": "885800.TI"}, "required": {"ts_code", "con_code"}, "limit": 1},
    {"module": "capital", "api": "top_list", "params": {"trade_date": SMOKE_DAY}, "limit": 1},
    {"module": "capital", "api": "top_inst", "params": {"trade_date": SMOKE_DAY}, "limit": 1},
    {"module": "margin", "api": "margin", "params": {"trade_date": SMOKE_DAY}, "limit": 1},
    {"module": "margin", "api": "margin_detail", "params": {"ts_code": SMOKE_STOCK, "trade_date": SMOKE_DAY}, "limit": 1},
    {"module": "risk", "api": "pledge_stat", "params": {"ts_code": SMOKE_STOCK}, "limit": 1},
    {"module": "risk", "api": "pledge_detail", "params": {"ts_code": SMOKE_STOCK}, "limit": 1},
    {"module": "risk", "api": "share_float", "params": {"ts_code": SMOKE_STOCK}, "limit": 1},
    {"module": "risk", "api": "repurchase", "params": {"ts_code": SMOKE_STOCK}, "limit": 1},
    {"module": "risk", "api": "stk_holdertrade", "params": {"ts_code": SMOKE_STOCK}, "limit": 1},
    {"module": "fund", "api": "fund_basic", "params": {"market": "E"}, "limit": 1},
    {"module": "fund", "api": "fund_daily", "params": {"ts_code": SMOKE_ETF, "start_date": SMOKE_DAY, "end_date": SMOKE_DAY}, "limit": 1},
    {"module": "option", "api": "opt_basic", "params": {"exchange": "SSE"}, "limit": 1},
    {"module": "feature", "api": "broker_recommend", "params": {"month": SMOKE_MONTH}, "limit": 1},
    {"module": "feature", "api": "cyq_chips", "params": {"ts_code": SMOKE_STOCK, "trade_date": SMOKE_DAY}, "limit": 1},
    {"module": "feature", "api": "stk_factor_pro", "params": {"ts_code": SMOKE_STOCK, "trade_date": SMOKE_DAY}, "limit": 1},
)


def run_probe(output: str | Path = REPORT_PATH) -> dict[str, Any]:
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
            limit=spec.get("limit", 1),
            use_cache=False,
        )
        results[spec["api"]] = {
            "module": spec["module"],
            "status": result.status,
            "row_count": len(result.records),
            "missing_fields": result.missing_fields,
            "error_type": result.error_type,
            "error_message": result.error_message,
        }

    finished = datetime.now(timezone.utc)
    report = _build_report(provider.token_configured(), results, started, finished, started_perf)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(output_path)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _build_report(
    token_configured: bool,
    results: dict[str, dict[str, Any]],
    started: datetime,
    finished: datetime,
    started_perf: float,
) -> dict[str, Any]:
    available = [api for api, item in results.items() if item["status"] == "available"]
    unavailable = [api for api, item in results.items() if item["status"] not in {"available", "empty"}]
    permission_errors = [api for api, item in results.items() if item["status"] == "permission_denied"]
    field_errors = [api for api, item in results.items() if item["status"] == "field_mismatch"]
    network_errors = [
        api
        for api, item in results.items()
        if item["status"] == "error" and item.get("error_type") == "NetworkError"
    ]
    suggestions = []
    if not token_configured:
        suggestions.append("TUSHARE_TOKEN is not configured")
    if permission_errors:
        suggestions.append("Some Tushare APIs need higher points or separate permission; optional factors can be skipped.")
    if field_errors:
        suggestions.append("Field mismatch detected; update provider mapping against official Tushare docs.")
    if network_errors:
        suggestions.append("Network errors detected; retry later or check proxy/VPN/DNS settings.")
    return {
        "token_configured": token_configured,
        "available_apis": available,
        "unavailable_apis": unavailable,
        "permission_errors": permission_errors,
        "field_errors": field_errors,
        "network_errors": network_errors,
        "results": results,
        "recommendations": suggestions,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round(time.perf_counter() - started_perf, 3),
        "report_path": str(REPORT_PATH),
    }


def main() -> int:
    report = run_probe()
    print(
        "summary: "
        f"token_configured={report['token_configured']} "
        f"available={len(report['available_apis'])} "
        f"unavailable={len(report['unavailable_apis'])} "
        f"permission_errors={len(report['permission_errors'])} "
        f"field_errors={len(report['field_errors'])} "
        f"network_errors={len(report['network_errors'])} "
        f"report_path={report['report_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
