from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from datasource.tushare_provider import TushareMarketDataProvider
from scripts.run_real_quant_top500 import _parse_bool_arg


REPORT_PATH = Path("data/reports/tushare_cache_prewarm_report.json")
DEFAULT_LOOKBACK_DAYS = 20
DEFAULT_INCLUDE = "stock_basic,trade_cal,daily,daily_basic,adj_factor,stk_limit,moneyflow,fina_indicator,concept,top_list,margin"
STOCK_BASIC_FIELDS = "ts_code,symbol,name,industry,list_status,exchange,market,list_date"
DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"


def run_prewarm(
    sample_limit: int = 0,
    start_date: str | None = None,
    end_date: str | None = None,
    refresh_cache: bool = False,
    incremental_days: int | None = None,
    include: str = DEFAULT_INCLUDE,
    output: str | Path = REPORT_PATH,
    progress: bool = True,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    provider = TushareMarketDataProvider(cache_enabled=True)
    provider.reset_cache_stats()
    warnings = ["no_llm_call_verified=true", "execution_mode=manual", "scheduler_disabled=true"]
    errors_sample: list[dict[str, Any]] = []
    interface_results: list[dict[str, Any]] = []
    include_set = _parse_include(include)
    can_use_cache = (not refresh_cache) and provider.token_configured()

    end = _resolve_end_date(provider, end_date, refresh_cache, can_use_cache)
    start = _resolve_start_date(start_date, end, incremental_days)
    stocks: list[dict[str, Any]] = []
    if "stock_basic" in include_set or _needs_stock_universe(include_set):
        stock_result = _query_and_record(
            provider,
            interface_results,
            "stock_basic",
            params={"exchange": "", "list_status": "L"},
            fields=STOCK_BASIC_FIELDS,
            required={"ts_code", "name"},
            use_cache=can_use_cache,
            refresh_cache=refresh_cache,
            errors_sample=errors_sample,
        )
        stocks = _filter_stock_rows(stock_result.records)
    if sample_limit > 0:
        stocks = stocks[:sample_limit]

    if "trade_cal" in include_set:
        _query_and_record(
            provider,
            interface_results,
            "trade_cal",
            params={"exchange": "", "start_date": _ts_date(start), "end_date": _ts_date(end)},
            fields="exchange,cal_date,is_open,pretrade_date",
            required={"cal_date", "is_open"},
            use_cache=can_use_cache,
            refresh_cache=refresh_cache,
            errors_sample=errors_sample,
        )

    for api_name, spec in _market_interface_specs(end).items():
        if api_name not in include_set:
            continue
        _query_and_record(
            provider,
            interface_results,
            spec["api"],
            params=spec.get("params", {}),
            fields=spec.get("fields"),
            required=spec.get("required", set()),
            use_cache=can_use_cache,
            refresh_cache=refresh_cache,
            errors_sample=errors_sample,
            display_name=api_name,
        )

    stock_specs = _stock_interface_specs(start, end)
    for api_name, spec in stock_specs.items():
        if api_name not in include_set:
            continue
        aggregate = _new_aggregate(api_name)
        for index, stock in enumerate(stocks, start=1):
            code = stock["ts_code"]
            if progress and (index == 1 or index % 100 == 0 or index == len(stocks)):
                print(f"[tushare-prewarm] {api_name} {index}/{len(stocks)} {code}")
            result_item = _query_single(
                provider,
                spec["api"],
                params=spec["params"](code),
                fields=spec.get("fields"),
                required=spec.get("required", set()),
                use_cache=can_use_cache,
                refresh_cache=refresh_cache,
                errors_sample=errors_sample,
                display_name=api_name,
                stock_code=code,
            )
            _merge_aggregate(aggregate, result_item)
        interface_results.append(_finish_aggregate(aggregate))

    finished_at = datetime.now(timezone.utc)
    summary = _summarize_interfaces(interface_results)
    report = {
        "provider": "tushare",
        "token_configured": provider.token_configured(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "sample_limit": sample_limit,
        "target_stock_count": len(stocks),
        "interface_results": interface_results,
        "success_count": summary["success_count"],
        "empty_count": summary["empty_count"],
        "permission_denied_count": summary["permission_denied_count"],
        "field_error_count": summary["field_error_count"],
        "network_error_count": summary["network_error_count"],
        "cache_hit_count": provider.cache_hit_count,
        "cache_miss_count": provider.cache_miss_count,
        "cache_insufficient_count": provider.cache_insufficient_count,
        "refreshed_count": summary["refreshed_count"],
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(time.perf_counter() - started_perf, 3),
        "warnings": warnings + summary["warnings"],
        "errors_sample": errors_sample[:50],
        "report_path": str(output_path),
        "no_llm_call_verified": True,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Prewarm Tushare cache for manual quant runs.")
    parser.add_argument("--sample-limit", type=int, default=0)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--refresh-cache", type=_parse_bool_arg, nargs="?", const=True, default=False)
    parser.add_argument("--incremental-days", type=int, default=None)
    parser.add_argument("--include", default=DEFAULT_INCLUDE)
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()

    report = run_prewarm(
        sample_limit=args.sample_limit,
        start_date=args.start_date,
        end_date=args.end_date,
        refresh_cache=args.refresh_cache,
        incremental_days=args.incremental_days,
        include=args.include,
        output=args.output,
    )
    print(
        "summary: "
        f"target_stock_count={report['target_stock_count']} success_count={report['success_count']} "
        f"empty_count={report['empty_count']} permission_denied_count={report['permission_denied_count']} "
        f"field_error_count={report['field_error_count']} network_error_count={report['network_error_count']} "
        f"cache_hit_count={report['cache_hit_count']} cache_miss_count={report['cache_miss_count']} "
        f"refreshed_count={report['refreshed_count']} report_path={report['report_path']}"
    )
    return 0


def _query_and_record(
    provider: TushareMarketDataProvider,
    interface_results: list[dict[str, Any]],
    api_name: str,
    params: dict[str, Any],
    fields: str | None = None,
    required: set[str] | None = None,
    use_cache: bool = True,
    refresh_cache: bool = False,
    errors_sample: list[dict[str, Any]] | None = None,
    display_name: str | None = None,
):
    result_item = _query_single(
        provider,
        api_name,
        params=params,
        fields=fields,
        required=required or set(),
        use_cache=use_cache,
        refresh_cache=refresh_cache,
        errors_sample=errors_sample,
        display_name=display_name,
    )
    interface_results.append(_public_result_item(result_item))
    return result_item["raw_result"]


def _query_single(
    provider: TushareMarketDataProvider,
    api_name: str,
    params: dict[str, Any],
    fields: str | None,
    required: set[str],
    use_cache: bool,
    refresh_cache: bool,
    errors_sample: list[dict[str, Any]] | None,
    display_name: str | None = None,
    stock_code: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    cache_path = provider._cache_path(api_name, {key: value for key, value in params.items() if value not in (None, "")}, fields)
    result = provider.query_endpoint(
        api_name,
        params=params,
        fields=fields,
        required_fields=required,
        use_cache=use_cache,
    )
    item = {
        "api_name": display_name or api_name,
        "status": result.status,
        "row_count": len(result.records),
        "cache_path": str(cache_path),
        "error_type": result.error_type,
        "error_message": result.error_message,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "refreshed": bool(refresh_cache and result.status in {"available", "empty"}),
        "raw_result": result,
    }
    if result.status == "empty":
        item["warning"] = "empty data"
    if result.status not in {"available", "empty"} and errors_sample is not None:
        _append_error(errors_sample, stock_code or item["api_name"], result.error_type or result.status, result.error_message or result.status)
    return item


def _market_interface_specs(end: date) -> dict[str, dict[str, Any]]:
    day = _ts_date(end)
    return {
        "concept": {"api": "ths_index", "params": {"exchange": "A", "type": "N"}, "required": {"ts_code", "name"}},
        "concept_detail": {"api": "ths_member", "params": {"ts_code": "885800.TI"}, "required": {"ts_code", "con_code"}},
        "top_list": {"api": "top_list", "params": {"trade_date": day}},
        "margin": {"api": "margin", "params": {"trade_date": day}},
        "broker_recommendation": {"api": "broker_recommend", "params": {"month": day[:6]}},
        "etf_list": {"api": "fund_basic", "params": {"market": "E"}, "required": {"ts_code", "name"}},
    }


def _stock_interface_specs(start: date, end: date) -> dict[str, dict[str, Any]]:
    start_text = _ts_date(start)
    end_text = _ts_date(end)
    return {
        "daily": {
            "api": "daily",
            "params": lambda code: {"ts_code": code, "start_date": start_text, "end_date": end_text},
            "fields": DAILY_FIELDS,
            "required": {"ts_code", "trade_date", "open", "high", "low", "close"},
        },
        "adj_factor": {
            "api": "adj_factor",
            "params": lambda code: {"ts_code": code, "start_date": start_text, "end_date": end_text},
            "fields": "ts_code,trade_date,adj_factor",
            "required": {"ts_code", "trade_date", "adj_factor"},
        },
        "daily_basic": {
            "api": "daily_basic",
            "params": lambda code: {"ts_code": code},
            "fields": "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv,limit_status",
            "required": {"ts_code", "trade_date"},
        },
        "stk_limit": {
            "api": "stk_limit",
            "params": lambda code: {"ts_code": code, "start_date": start_text, "end_date": end_text},
            "fields": "trade_date,ts_code,pre_close,up_limit,down_limit",
            "required": {"ts_code", "trade_date"},
        },
        "moneyflow": {
            "api": "moneyflow",
            "params": lambda code: {"ts_code": code},
            "required": {"ts_code", "trade_date"},
        },
        "fina_indicator": {"api": "fina_indicator", "params": lambda code: {"ts_code": code}},
        "margin_detail": {
            "api": "margin_detail",
            "params": lambda code: {"ts_code": code, "trade_date": end_text},
        },
        "pledge": {"api": "pledge_stat", "params": lambda code: {"ts_code": code}},
        "share_unlock": {"api": "share_float", "params": lambda code: {"ts_code": code}},
        "repurchase": {"api": "repurchase", "params": lambda code: {"ts_code": code}},
        "holder_trade": {"api": "stk_holdertrade", "params": lambda code: {"ts_code": code}},
        "chip_distribution": {"api": "cyq_chips", "params": lambda code: {"ts_code": code, "trade_date": end_text}},
        "quant_factor": {"api": "stk_factor_pro", "params": lambda code: {"ts_code": code, "trade_date": end_text}},
        "etf_daily": {"api": "fund_daily", "params": lambda code: {"ts_code": "510300.SH", "start_date": start_text, "end_date": end_text}},
    }


def _parse_include(value: str) -> set[str]:
    aliases = {
        "ths_index": "concept",
        "ths_member": "concept_detail",
        "concept_detail": "concept_detail",
        "pledge_stat": "pledge",
        "share_float": "share_unlock",
        "cyq_chips": "chip_distribution",
        "stk_factor_pro": "quant_factor",
        "fund_basic": "etf_list",
        "fund_daily": "etf_daily",
    }
    raw = [item.strip() for item in str(value or "").split(",") if item.strip()]
    parsed = {aliases.get(item, item) for item in raw}
    if "concept" in parsed:
        parsed.add("concept_detail")
    return parsed


def _needs_stock_universe(include_set: set[str]) -> bool:
    return bool(include_set & set(_stock_interface_specs(date.today(), date.today())))


def _filter_stock_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stocks = []
    for row in rows:
        ts_code = str(row.get("ts_code") or "").strip().upper()
        name = str(row.get("name") or "")
        status = str(row.get("list_status") or "L").upper()
        if not ts_code or "ST" in name.upper() or status not in {"L", "NORMAL", "LISTED", "1"}:
            continue
        stocks.append({"ts_code": ts_code, "name": name})
    return stocks


def _resolve_end_date(provider: TushareMarketDataProvider, value: str | None, refresh_cache: bool, can_use_cache: bool) -> date:
    if value:
        return _parse_date_value(value)
    today = date.today()
    result = provider.query_endpoint(
        "trade_cal",
        params={"exchange": "", "start_date": _ts_date(today - timedelta(days=20)), "end_date": _ts_date(today), "is_open": "1"},
        fields="cal_date,is_open",
        required_fields={"cal_date", "is_open"},
        use_cache=can_use_cache,
    )
    open_days = sorted(str(row.get("cal_date")) for row in result.records if str(row.get("is_open")) in {"1", "True", "true"})
    if open_days:
        return _parse_date_value(open_days[-1])
    return today


def _resolve_start_date(value: str | None, end: date, incremental_days: int | None) -> date:
    if incremental_days is not None:
        return end - timedelta(days=max(0, incremental_days))
    if value:
        return _parse_date_value(value)
    return end - timedelta(days=DEFAULT_LOOKBACK_DAYS)


def _new_aggregate(api_name: str) -> dict[str, Any]:
    return {
        "api_name": api_name,
        "statuses": {},
        "row_count": 0,
        "cache_path": "",
        "error_type": None,
        "error_message": None,
        "duration_seconds": 0.0,
        "refreshed": False,
    }


def _merge_aggregate(aggregate: dict[str, Any], item: dict[str, Any]) -> None:
    status = item["status"]
    aggregate["statuses"][status] = aggregate["statuses"].get(status, 0) + 1
    aggregate["row_count"] += int(item["row_count"])
    aggregate["duration_seconds"] += float(item["duration_seconds"])
    aggregate["refreshed"] = aggregate["refreshed"] or bool(item["refreshed"])
    if not aggregate["cache_path"]:
        aggregate["cache_path"] = item["cache_path"]
    if status not in {"available", "empty"} and aggregate["error_type"] is None:
        aggregate["error_type"] = item["error_type"]
        aggregate["error_message"] = item["error_message"]


def _finish_aggregate(aggregate: dict[str, Any]) -> dict[str, Any]:
    statuses = aggregate.pop("statuses")
    if not statuses:
        status = "empty"
    elif any(key in statuses for key in ("error", "not_configured")):
        status = "error"
    elif "permission_denied" in statuses:
        status = "permission_denied"
    elif "field_mismatch" in statuses:
        status = "field_mismatch"
    elif set(statuses) == {"empty"}:
        status = "empty"
    else:
        status = "available"
    aggregate["status"] = status
    aggregate["duration_seconds"] = round(aggregate["duration_seconds"], 3)
    return aggregate


def _public_result_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "raw_result"}


def _summarize_interfaces(items: list[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    success_count = sum(1 for item in items if item["status"] == "available")
    empty_count = sum(1 for item in items if item["status"] == "empty")
    permission_denied_count = sum(1 for item in items if item["status"] == "permission_denied")
    field_error_count = sum(1 for item in items if item["status"] == "field_mismatch")
    network_error_count = sum(1 for item in items if item.get("error_type") == "NetworkError")
    refreshed_count = sum(1 for item in items if item.get("refreshed"))
    for item in items:
        if item["status"] == "empty":
            warnings.append(f"{item['api_name']} returned empty data")
    return {
        "success_count": success_count,
        "empty_count": empty_count,
        "permission_denied_count": permission_denied_count,
        "field_error_count": field_error_count,
        "network_error_count": network_error_count,
        "refreshed_count": refreshed_count,
        "warnings": warnings[:50],
    }


def _parse_date_value(value: str | date) -> date:
    if isinstance(value, date):
        return value
    normalized = str(value).split("T", 1)[0]
    if "-" in normalized:
        return date.fromisoformat(normalized)
    return date(int(normalized[:4]), int(normalized[4:6]), int(normalized[6:8]))


def _ts_date(value: str | date) -> str:
    return _parse_date_value(value).strftime("%Y%m%d")


def _append_error(errors: list[dict[str, Any]], target: str, code: str, message: str) -> None:
    if len(errors) < 50:
        errors.append({"target": target, "code": code, "message": message})


if __name__ == "__main__":
    raise SystemExit(main())
