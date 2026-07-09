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

from datasource.baostock_provider import BaoStockMarketDataProvider
from datasource.models.market import MarketStockInfo
from scripts.run_real_quant_top500 import _parse_bool_arg


REPORT_PATH = Path("data/reports/baostock_kline_cache_prewarm_report.json")
DEFAULT_LOOKBACK_DAYS = 20


def run_prewarm(
    sample_limit: int = 0,
    start_date: str | None = None,
    end_date: str | None = None,
    max_lookback_days: int = 20,
    frequency: str = "daily",
    refresh_cache: bool = False,
    incremental_days: int | None = None,
    output: str | Path = REPORT_PATH,
    progress: bool = True,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    provider = BaoStockMarketDataProvider(refresh_kline_cache=refresh_cache)
    warnings = ["no_llm_call_verified=true", "execution_mode=manual", "scheduler_disabled=true"]
    errors_sample: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0
    skipped_count = 0
    refreshed_count = 0

    with provider.batch_session():
        stocks = provider.get_stock_list(max_lookback_days=max_lookback_days)
        actual_trade_date = provider.last_actual_trade_date
        end = _resolve_end_date(end_date, actual_trade_date)
        start = _resolve_start_date(start_date, end, incremental_days)
        target_stocks = _filter_prewarm_stocks(stocks)
        if sample_limit > 0:
            target_stocks = target_stocks[:sample_limit]

        for index, stock in enumerate(target_stocks, start=1):
            if progress and (index == 1 or index % 50 == 0 or index == len(target_stocks)):
                print(f"[baostock-prewarm] {index}/{len(target_stocks)} {stock.code}")
            try:
                if incremental_days is not None:
                    bars = _incremental_update(provider, stock.code, start, end, frequency)
                    refreshed_count += 1
                else:
                    bars = provider.get_kline(stock.code, start.isoformat(), end.isoformat(), frequency=frequency)
                    if refresh_cache:
                        refreshed_count += 1
                if bars:
                    success_count += 1
                else:
                    skipped_count += 1
                    _append_error(errors_sample, stock.code, "EMPTY_KLINE", "BaoStock returned no kline bars")
            except Exception as exc:
                failed_count += 1
                _append_error(errors_sample, stock.code, exc.__class__.__name__, str(exc))

    finished_at = datetime.now(timezone.utc)
    duration_seconds = round(time.perf_counter() - started, 3)
    target_count = len(_filter_prewarm_stocks(stocks))
    if sample_limit > 0:
        target_count = min(target_count, sample_limit)
    report = {
        "provider": "baostock",
        "history_provider": "baostock",
        "actual_trade_date": provider.last_actual_trade_date,
        "universe_count": len(stocks),
        "target_count": target_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "cache_hit_count": provider.cache_hit_count,
        "cache_miss_count": provider.cache_miss_count,
        "cache_insufficient_count": provider.cache_insufficient_count,
        "cache_refresh_count": provider.cache_refresh_count,
        "refreshed_count": refreshed_count,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": duration_seconds,
        "avg_stock_seconds": round(duration_seconds / target_count, 3) if target_count else 0.0,
        "errors_sample": errors_sample[:50],
        "warnings": warnings,
        "cache_dir": str(provider.kline_cache_dir),
        "report_path": str(output_path),
        "no_llm_call_verified": True,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Prewarm BaoStock kline cache for manual quant runs.")
    parser.add_argument("--sample-limit", type=int, default=0)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-lookback-days", type=int, default=20)
    parser.add_argument("--frequency", default="daily")
    parser.add_argument("--refresh-cache", type=_parse_bool_arg, nargs="?", const=True, default=False)
    parser.add_argument("--incremental-days", type=int, default=None)
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()

    report = run_prewarm(
        sample_limit=args.sample_limit,
        start_date=args.start_date,
        end_date=args.end_date,
        max_lookback_days=args.max_lookback_days,
        frequency=args.frequency,
        refresh_cache=args.refresh_cache,
        incremental_days=args.incremental_days,
        output=args.output,
    )
    print(
        "summary: "
        f"target_count={report['target_count']} success_count={report['success_count']} "
        f"failed_count={report['failed_count']} skipped_count={report['skipped_count']} "
        f"cache_hit_count={report['cache_hit_count']} cache_miss_count={report['cache_miss_count']} "
        f"refreshed_count={report['refreshed_count']} report_path={report['report_path']}"
    )
    return 0


def _incremental_update(
    provider: BaoStockMarketDataProvider,
    stock_code: str,
    start: date,
    end: date,
    frequency: str,
) -> list:
    previous = provider.read_all_cached_kline(stock_code, frequency=frequency)
    original_refresh = provider.refresh_kline_cache
    provider.refresh_kline_cache = True
    try:
        latest = provider.get_kline(stock_code, start.isoformat(), end.isoformat(), frequency=frequency)
    finally:
        provider.refresh_kline_cache = original_refresh
    combined = previous + latest
    if not combined:
        return []
    dates = [_parse_date_value(bar.datetime) for bar in combined]
    provider.write_kline_cache(
        stock_code,
        min(dates).isoformat(),
        max(dates).isoformat(),
        frequency,
        "3",
        combined,
    )
    return combined


def _filter_prewarm_stocks(stocks: list[MarketStockInfo]) -> list[MarketStockInfo]:
    filtered = []
    for stock in stocks:
        if not stock.code or len(stock.code) != 6:
            continue
        if "ST" in stock.name.upper() or stock.status.upper() not in {"NORMAL", "1", "LISTED"}:
            continue
        filtered.append(stock)
    return filtered


def _resolve_end_date(value: str | None, actual_trade_date: str | None) -> date:
    if value:
        return _parse_date_value(value)
    if actual_trade_date:
        return _parse_date_value(actual_trade_date)
    return date.today()


def _resolve_start_date(value: str | None, end: date, incremental_days: int | None) -> date:
    if incremental_days is not None:
        return end - timedelta(days=max(0, incremental_days))
    if value:
        return _parse_date_value(value)
    return end - timedelta(days=DEFAULT_LOOKBACK_DAYS)


def _parse_date_value(value: str) -> date:
    normalized = str(value).split("T", 1)[0]
    if "-" in normalized:
        return date.fromisoformat(normalized)
    return date(int(normalized[:4]), int(normalized[4:6]), int(normalized[6:8]))


def _append_error(errors: list[dict[str, Any]], stock_code: str, code: str, message: str) -> None:
    if len(errors) < 50:
        errors.append({"stock_code": stock_code, "code": code, "message": message})


if __name__ == "__main__":
    raise SystemExit(main())
