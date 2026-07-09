from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from datasource.baostock_provider import BaoStockMarketDataProvider
from datasource.models.market import KLineBar, MarketStockInfo
from scripts.prewarm_baostock_kline_cache import _filter_prewarm_stocks


REPORT_PATH = Path("data/reports/baostock_kline_cache_quality_report.json")


def run_quality_check(
    sample_limit: int = 0,
    min_bars: int = 60,
    frequency: str = "daily",
    output: str | Path = REPORT_PATH,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    provider = BaoStockMarketDataProvider()
    stocks = _filter_prewarm_stocks(provider.get_stock_list())
    if sample_limit > 0:
        stocks = stocks[:sample_limit]

    valid_cache_count = 0
    missing_count = 0
    insufficient_count = 0
    duplicate_date_count = 0
    unsorted_count = 0
    invalid_date_count = 0
    invalid_ohlc_count = 0
    errors_sample: list[dict[str, Any]] = []

    for stock in stocks:
        paths = provider._matching_kline_cache_paths(stock.code, frequency, "3")
        raw_bars = []
        file_problems = []
        for path in paths:
            file_bars = provider._read_kline_cache_file(path)
            raw_bars.extend(file_bars)
            file_problems.append(_validate_single_file_order(file_bars))
        merged_bars = provider.read_all_cached_kline(stock.code, frequency=frequency)
        if not raw_bars:
            missing_count += 1
            _append_error(errors_sample, stock.code, "MISSING_CACHE", "No kline cache file found")
            continue

        problems = _validate_bars(raw_bars, merged_bars, min_bars)
        problems["duplicate_dates"] = any(item["duplicate_dates"] for item in file_problems)
        problems["unsorted"] = any(item["unsorted"] for item in file_problems)
        if problems["insufficient"]:
            insufficient_count += 1
            _append_error(errors_sample, stock.code, "INSUFFICIENT_BARS", f"bars={len(merged_bars)}")
        if problems["duplicate_dates"]:
            duplicate_date_count += 1
            _append_error(errors_sample, stock.code, "DUPLICATE_DATE", "Duplicate kline date found")
        if problems["unsorted"]:
            unsorted_count += 1
            _append_error(errors_sample, stock.code, "UNSORTED_DATES", "Kline cache is not sorted by date")
        if problems["invalid_dates"]:
            invalid_date_count += 1
            _append_error(errors_sample, stock.code, "INVALID_DATE", "Kline date cannot be parsed")
        if problems["invalid_ohlc"]:
            invalid_ohlc_count += 1
            _append_error(errors_sample, stock.code, "INVALID_OHLC", "OHLC field cannot be parsed as number")
        if not any(problems.values()):
            valid_cache_count += 1

    finished_at = datetime.now(timezone.utc)
    report = {
        "provider": "baostock",
        "history_provider": "baostock",
        "checked_count": len(stocks),
        "valid_cache_count": valid_cache_count,
        "missing_count": missing_count,
        "insufficient_count": insufficient_count,
        "duplicate_date_count": duplicate_date_count,
        "unsorted_count": unsorted_count,
        "invalid_date_count": invalid_date_count,
        "invalid_ohlc_count": invalid_ohlc_count,
        "min_bars": min_bars,
        "frequency": frequency,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "errors_sample": errors_sample[:50],
        "warnings": ["no_llm_call_verified=true", "execution_mode=manual", "scheduler_disabled=true"],
        "cache_dir": str(provider.kline_cache_dir),
        "report_path": str(output_path),
        "no_llm_call_verified": True,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Check BaoStock kline cache quality.")
    parser.add_argument("--sample-limit", type=int, default=0)
    parser.add_argument("--min-bars", type=int, default=60)
    parser.add_argument("--frequency", default="daily")
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()

    report = run_quality_check(
        sample_limit=args.sample_limit,
        min_bars=args.min_bars,
        frequency=args.frequency,
        output=args.output,
    )
    print(
        "summary: "
        f"checked_count={report['checked_count']} valid_cache_count={report['valid_cache_count']} "
        f"missing_count={report['missing_count']} insufficient_count={report['insufficient_count']} "
        f"duplicate_date_count={report['duplicate_date_count']} report_path={report['report_path']}"
    )
    return 0


def _validate_bars(raw_bars: list[KLineBar], merged_bars: list[KLineBar], min_bars: int) -> dict[str, bool]:
    parsed_dates: list[date] = []
    invalid_dates = False
    invalid_ohlc = False
    for bar in raw_bars:
        try:
            parsed_dates.append(_parse_date_value(bar.datetime))
        except (TypeError, ValueError):
            invalid_dates = True
        for value in (bar.open, bar.high, bar.low, bar.close):
            try:
                float(value)
            except (TypeError, ValueError):
                invalid_ohlc = True
    return {
        "insufficient": len(merged_bars) < min_bars,
        "duplicate_dates": False,
        "unsorted": False,
        "invalid_dates": invalid_dates,
        "invalid_ohlc": invalid_ohlc,
    }


def _validate_single_file_order(bars: list[KLineBar]) -> dict[str, bool]:
    parsed_dates = []
    for bar in bars:
        try:
            parsed_dates.append(_parse_date_value(bar.datetime))
        except (TypeError, ValueError):
            continue
    return {
        "duplicate_dates": len(parsed_dates) != len(set(parsed_dates)),
        "unsorted": parsed_dates != sorted(parsed_dates),
    }


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
