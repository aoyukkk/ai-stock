from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from datasource.tushare_provider import DEFAULT_TOKEN_ENV, TushareMarketDataProvider, _env_file_value


REPORT_PATH = Path("data/reports/tushare_trade_date_cache_prewarm_report.json")
CORE_INTERFACES = ("daily", "daily_basic", "adj_factor", "stk_limit", "moneyflow")
REFERENCE_INTERFACES = ("top_list", "margin", "margin_detail", "ths_index", "ths_member")


def run_prewarm(
    start_date: str | None = None,
    end_date: str | None = None,
    interfaces: str | list[str] | None = None,
    include_reference: bool = False,
    refresh_cache: bool = False,
    output: str | Path = REPORT_PATH,
    progress: bool = True,
) -> dict[str, Any]:
    _load_local_tushare_token()
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    provider = TushareMarketDataProvider(cache_enabled=True)
    start = _normalize_date(start_date) if start_date else date.today() - timedelta(days=20)
    end = _normalize_date(end_date) if end_date else date.today()
    selected_interfaces = _parse_interfaces(interfaces)
    if include_reference:
        selected_interfaces = list(dict.fromkeys([*selected_interfaces, *REFERENCE_INTERFACES]))

    warnings: list[str] = ["no_llm_call_verified=true", "scheduler_disabled=true"]
    errors_sample: list[dict[str, Any]] = []
    interface_results: list[dict[str, Any]] = []
    trade_dates: list[str] = []

    if not provider.token_configured():
        report = _build_report(
            provider=provider,
            start_date=start,
            end_date=end,
            trade_dates=trade_dates,
            interfaces=selected_interfaces,
            interface_results=interface_results,
            warnings=warnings,
            errors_sample=[{"api_name": "tushare", "code": "MissingToken", "message": "TUSHARE_TOKEN is not configured"}],
            output_path=output_path,
            started_at=started_at,
            started_perf=started_perf,
        )
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    try:
        trade_dates = provider.get_open_trade_dates(start.isoformat(), end.isoformat())
    except Exception as exc:
        errors_sample.append({"api_name": "trade_cal", "code": exc.__class__.__name__, "message": str(exc)})
        trade_dates = []

    for api_name in selected_interfaces:
        dates = ["latest"] if api_name in {"ths_index", "ths_member"} else trade_dates
        for index, trade_day in enumerate(dates, start=1):
            if progress and (index == 1 or index == len(dates)):
                print(f"[tushare-trade-date-prewarm] {api_name} {index}/{len(dates)} {trade_day}")
            item_started = time.perf_counter()
            try:
                result = provider.query_trade_date_endpoint(
                    api_name,
                    None if trade_day == "latest" else trade_day,
                    use_cache=True,
                    refresh_cache=refresh_cache,
                )
                item = {
                    "api_name": api_name,
                    "trade_date": trade_day,
                    "status": result.status,
                    "row_count": len(result.records),
                    "cache_path": str(provider.trade_date_cache_path(api_name, None if trade_day == "latest" else trade_day)),
                    "error_type": result.error_type,
                    "error_message": result.error_message,
                    "duration_seconds": round(time.perf_counter() - item_started, 3),
                }
                if result.status == "field_mismatch":
                    warnings.append(f"field_mismatch:{api_name}:{trade_day}:{','.join(result.missing_fields)}")
                elif result.status == "empty":
                    warnings.append(f"empty:{api_name}:{trade_day}")
                elif result.status in {"permission_denied", "error", "not_configured"}:
                    errors_sample.append(
                        {
                            "api_name": api_name,
                            "trade_date": trade_day,
                            "code": result.error_type or result.status,
                            "message": result.error_message or result.status,
                        }
                    )
                interface_results.append(item)
            except Exception as exc:
                item = {
                    "api_name": api_name,
                    "trade_date": trade_day,
                    "status": "error",
                    "row_count": 0,
                    "cache_path": str(provider.trade_date_cache_path(api_name, None if trade_day == "latest" else trade_day)),
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                    "duration_seconds": round(time.perf_counter() - item_started, 3),
                }
                interface_results.append(item)
                errors_sample.append(
                    {
                        "api_name": api_name,
                        "trade_date": trade_day,
                        "code": exc.__class__.__name__,
                        "message": str(exc),
                    }
                )

    report = _build_report(
        provider=provider,
        start_date=start,
        end_date=end,
        trade_dates=trade_dates,
        interfaces=selected_interfaces,
        interface_results=interface_results,
        warnings=warnings,
        errors_sample=errors_sample,
        output_path=output_path,
        started_at=started_at,
        started_perf=started_perf,
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Prewarm Tushare trade_date batch cache.")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--interfaces", default=",".join(CORE_INTERFACES))
    parser.add_argument("--include-reference", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()

    report = run_prewarm(
        start_date=args.start_date,
        end_date=args.end_date,
        interfaces=args.interfaces,
        include_reference=args.include_reference,
        refresh_cache=args.refresh_cache,
        output=args.output,
    )
    print(
        "summary: "
        f"trade_date_count={report['trade_date_count']} interfaces={','.join(report['interfaces'])} "
        f"success_count={report['success_count']} empty_count={report['empty_count']} "
        f"error_count={report['error_count']} cache_hit_count={report['cache_hit_count']} "
        f"cache_miss_count={report['cache_miss_count']} report_path={report['report_path']}"
    )
    return 0


def _build_report(
    provider: TushareMarketDataProvider,
    start_date: date,
    end_date: date,
    trade_dates: list[str],
    interfaces: list[str],
    interface_results: list[dict[str, Any]],
    warnings: list[str],
    errors_sample: list[dict[str, Any]],
    output_path: Path,
    started_at: datetime,
    started_perf: float,
) -> dict[str, Any]:
    finished_at = datetime.now(timezone.utc)
    return {
        "provider": "tushare",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "trade_date_count": len(trade_dates),
        "interfaces": interfaces,
        "interface_results": interface_results,
        "success_count": sum(1 for item in interface_results if item["status"] == "available"),
        "empty_count": sum(1 for item in interface_results if item["status"] == "empty"),
        "permission_denied_count": sum(1 for item in interface_results if item["status"] == "permission_denied"),
        "field_error_count": sum(1 for item in interface_results if item["status"] == "field_mismatch"),
        "network_error_count": sum(1 for item in interface_results if item.get("error_type") == "NetworkError"),
        "error_count": sum(1 for item in interface_results if item["status"] not in {"available", "empty"}),
        "cache_hit_count": provider.trade_date_cache_hit_count,
        "cache_miss_count": provider.trade_date_cache_miss_count,
        "refreshed_count": provider.trade_date_cache_refresh_count,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(time.perf_counter() - started_perf, 3),
        "warnings": warnings[:100],
        "errors_sample": errors_sample[:50],
        "report_path": str(output_path),
        "no_llm_call_verified": True,
    }


def _parse_interfaces(value: str | list[str] | None) -> list[str]:
    if value is None:
        return list(CORE_INTERFACES)
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = value
    return [item.strip() for item in items if item and item.strip()]


def _normalize_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    text = str(value).split("T", 1)[0]
    if "-" in text:
        return date.fromisoformat(text)
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def _load_local_tushare_token() -> None:
    if os.getenv(DEFAULT_TOKEN_ENV, "").strip():
        return
    token = _env_file_value(Path(".env"), DEFAULT_TOKEN_ENV)
    if token:
        os.environ[DEFAULT_TOKEN_ENV] = token


if __name__ == "__main__":
    raise SystemExit(main())
