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

from datasource.baostock_provider import BaoStockMarketDataProvider
from datasource.models.market import KLineBar, MarketStockInfo
from datasource.tushare_provider import DEFAULT_TOKEN_ENV, TushareMarketDataProvider, _env_file_value


REPORT_PATH = Path("data/reports/tushare_baostock_kline_compare_report.json")
DEFAULT_LOOKBACK_DAYS = 20


def run_compare(
    sample_limit: int = 50,
    start_date: str | None = None,
    end_date: str | None = None,
    tolerance: float = 0.01,
    output: str | Path = REPORT_PATH,
    progress: bool = True,
) -> dict[str, Any]:
    _load_local_tushare_token()
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tushare = TushareMarketDataProvider(cache_enabled=True)
    baostock = BaoStockMarketDataProvider()
    end = _parse_date_value(end_date) if end_date else date.today()
    start = _parse_date_value(start_date) if start_date else end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    warnings = ["diagnostic_only=true", "no_llm_call_verified=true", "scheduler_disabled=true"]
    errors_sample: list[dict[str, Any]] = []
    sample_count = 0
    matched_count = 0
    mismatch_count = 0
    missing_in_tushare = 0
    missing_in_baostock = 0
    price_mismatch_count = 0
    volume_mismatch_count = 0
    amount_mismatch_count = 0

    stocks = _filter_stocks(tushare.get_stock_list())
    if sample_limit > 0:
        stocks = stocks[:sample_limit]

    with baostock.batch_session():
        for index, stock in enumerate(stocks, start=1):
            if progress and (index == 1 or index % 50 == 0 or index == len(stocks)):
                print(f"[kline-compare] {index}/{len(stocks)} {stock.code}")
            try:
                tushare_bars = tushare.get_kline(stock.code, start.isoformat(), end.isoformat())
                baostock_bars = baostock.get_kline(stock.code, start.isoformat(), end.isoformat())
            except Exception as exc:
                _append_error(errors_sample, stock.code, exc.__class__.__name__, str(exc))
                continue
            sample_count += 1
            t_by_date = {_bar_date(bar): bar for bar in tushare_bars}
            b_by_date = {_bar_date(bar): bar for bar in baostock_bars}
            t_dates = set(t_by_date)
            b_dates = set(b_by_date)
            missing_t = sorted(b_dates - t_dates)
            missing_b = sorted(t_dates - b_dates)
            missing_in_tushare += len(missing_t)
            missing_in_baostock += len(missing_b)
            stock_has_mismatch = bool(missing_t or missing_b)
            for trade_day in sorted(t_dates & b_dates):
                result = _compare_bar(t_by_date[trade_day], b_by_date[trade_day], tolerance)
                if result["price_mismatch"]:
                    price_mismatch_count += 1
                    stock_has_mismatch = True
                    _append_error(errors_sample, stock.code, "PRICE_MISMATCH", f"{trade_day}: {result['message']}")
                if result["volume_mismatch"]:
                    volume_mismatch_count += 1
                    stock_has_mismatch = True
                    _append_error(errors_sample, stock.code, "VOLUME_MISMATCH", f"{trade_day}: volume order differs")
                if result["amount_mismatch"]:
                    amount_mismatch_count += 1
                    stock_has_mismatch = True
                    _append_error(errors_sample, stock.code, "AMOUNT_MISMATCH", f"{trade_day}: amount order differs")
            if missing_t:
                _append_error(errors_sample, stock.code, "MISSING_IN_TUSHARE", ",".join(missing_t[:5]))
            if missing_b:
                _append_error(errors_sample, stock.code, "MISSING_IN_BAOSTOCK", ",".join(missing_b[:5]))
            if stock_has_mismatch:
                mismatch_count += 1
            else:
                matched_count += 1

    finished_at = datetime.now(timezone.utc)
    report = {
        "provider": "tushare",
        "backup_provider": "baostock",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "sample_count": sample_count,
        "matched_count": matched_count,
        "mismatch_count": mismatch_count,
        "missing_in_tushare": missing_in_tushare,
        "missing_in_baostock": missing_in_baostock,
        "price_mismatch_count": price_mismatch_count,
        "volume_mismatch_count": volume_mismatch_count,
        "amount_mismatch_count": amount_mismatch_count,
        "tolerance": tolerance,
        "warnings": warnings,
        "errors_sample": errors_sample[:50],
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(time.perf_counter() - started_perf, 3),
        "report_path": str(output_path),
        "no_llm_call_verified": True,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare sampled Tushare and BaoStock daily kline data.")
    parser.add_argument("--sample-limit", type=int, default=50)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--tolerance", type=float, default=0.01)
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()

    report = run_compare(
        sample_limit=args.sample_limit,
        start_date=args.start_date,
        end_date=args.end_date,
        tolerance=args.tolerance,
        output=args.output,
    )
    print(
        "summary: "
        f"sample_count={report['sample_count']} matched_count={report['matched_count']} "
        f"mismatch_count={report['mismatch_count']} missing_in_tushare={report['missing_in_tushare']} "
        f"missing_in_baostock={report['missing_in_baostock']} report_path={report['report_path']}"
    )
    return 0


def _compare_bar(t_bar: KLineBar, b_bar: KLineBar, tolerance: float) -> dict[str, Any]:
    price_fields = ("open", "high", "low", "close")
    price_problems = [
        field
        for field in price_fields
        if abs(float(getattr(t_bar, field)) - float(getattr(b_bar, field))) > tolerance
    ]
    return {
        "price_mismatch": bool(price_problems),
        "volume_mismatch": not _same_order(_normalized_volume(t_bar), _normalized_volume(b_bar)),
        "amount_mismatch": not _same_order(_normalized_amount(t_bar), _normalized_amount(b_bar)),
        "message": ",".join(price_problems),
    }


def _same_order(left: float, right: float) -> bool:
    if left == 0 and right == 0:
        return True
    if left <= 0 or right <= 0:
        return False
    ratio = max(left, right) / min(left, right)
    return ratio <= 10


def _normalized_volume(bar: KLineBar) -> float:
    value = float(bar.volume)
    return value * 100 if getattr(bar, "source", "") == "tushare" else value


def _normalized_amount(bar: KLineBar) -> float:
    value = float(bar.amount)
    return value * 1000 if getattr(bar, "source", "") == "tushare" else value


def _filter_stocks(stocks: list[MarketStockInfo]) -> list[MarketStockInfo]:
    filtered = []
    for stock in stocks:
        if not stock.code or len(stock.code) != 6:
            continue
        if "ST" in stock.name.upper() or stock.status.upper() not in {"NORMAL", "1", "LISTED"}:
            continue
        filtered.append(stock)
    return filtered


def _bar_date(bar: KLineBar) -> str:
    return _parse_date_value(bar.datetime).isoformat()


def _parse_date_value(value: str | date) -> date:
    if isinstance(value, date):
        return value
    normalized = str(value).split("T", 1)[0]
    if "-" in normalized:
        return date.fromisoformat(normalized)
    return date(int(normalized[:4]), int(normalized[4:6]), int(normalized[6:8]))


def _append_error(errors: list[dict[str, Any]], stock_code: str, code: str, message: str) -> None:
    if len(errors) < 50:
        errors.append({"stock_code": stock_code, "code": code, "message": message})


def _load_local_tushare_token() -> None:
    if os.getenv(DEFAULT_TOKEN_ENV, "").strip():
        return
    token = _env_file_value(Path(".env"), DEFAULT_TOKEN_ENV)
    if token:
        os.environ[DEFAULT_TOKEN_ENV] = token


if __name__ == "__main__":
    raise SystemExit(main())
