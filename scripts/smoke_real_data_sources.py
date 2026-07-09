from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from datasource.akshare_provider import AKShareMarketDataProvider
from datasource.baostock_provider import BaoStockMarketDataProvider
from scripts.network_diagnostics import proxy_env_detected


REPORT_PATH = Path("data/reports/real_data_source_smoke_report.json")


def run_smoke_real_data_sources(
    check_akshare: bool = True,
    check_baostock: bool = True,
    limit: int = 5,
    output: str | Path = REPORT_PATH,
    no_proxy: bool = False,
    trade_date: str | None = None,
    max_lookback_days: int = 15,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Path("data/cache/akshare").mkdir(parents=True, exist_ok=True)
    Path("data/cache/baostock").mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    errors_sample: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "akshare_available": False,
        "akshare_universe_count": 0,
        "akshare_proxy_mode": "no_proxy" if no_proxy else "env",
        "akshare_proxy_env_detected": proxy_env_detected(),
        "akshare_error_type": None,
        "akshare_error_message": None,
        "akshare_suggestion": None,
        "baostock_available": False,
        "baostock_login": False,
        "baostock_actual_trade_date": None,
        "baostock_date_attempts": [],
        "baostock_universe_count": 0,
        "baostock_kline_success_count": 0,
        "baostock_kline_failed_count": 0,
        "requested_trade_date": trade_date,
        "max_lookback_days": max_lookback_days,
        "warnings": warnings,
        "errors_sample": errors_sample,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "duration_seconds": 0,
    }

    akshare_stocks = []
    if check_akshare:
        ak_provider = AKShareMarketDataProvider(proxy_mode="no_proxy" if no_proxy else "env")
        try:
            akshare_stocks = ak_provider.get_stock_list()
            report["akshare_available"] = True
            report["akshare_universe_count"] = len(akshare_stocks)
            if len(akshare_stocks) < 4000:
                warnings.append(f"AKShare universe count below 4000: {len(akshare_stocks)}")
        except Exception as exc:
            diagnostics = ak_provider.diagnostics()
            report["akshare_error_type"] = diagnostics["error_type"] or exc.__class__.__name__
            report["akshare_error_message"] = diagnostics["error_message"] or str(exc)
            report["akshare_proxy_env_detected"] = diagnostics["proxy_env_detected"]
            report["akshare_suggestion"] = _akshare_suggestion(diagnostics)
            _append_error(errors_sample, "akshare", exc)

    baostock_stocks = []
    if check_baostock:
        provider = BaoStockMarketDataProvider()
        try:
            baostock_stocks = provider.get_stock_list(
                trade_date=trade_date,
                max_lookback_days=max_lookback_days,
            )
            report["baostock_available"] = True
            report["baostock_login"] = True
            report["baostock_actual_trade_date"] = provider.last_actual_trade_date
            report["baostock_date_attempts"] = provider.last_date_attempts
            report["baostock_universe_count"] = len(baostock_stocks)
            if len(baostock_stocks) == 0:
                warnings.append(
                    provider.last_error_message
                    or "BaoStock login succeeded but stock universe is empty; likely trade-date issue."
                )
        except Exception as exc:
            report["baostock_date_attempts"] = provider.last_date_attempts
            report["baostock_actual_trade_date"] = provider.last_actual_trade_date
            _append_error(errors_sample, "baostock_stock_list", exc)

        end = date.today()
        start = end - timedelta(days=30)
        for stock in baostock_stocks[:limit]:
            try:
                bars = provider.get_kline(stock.code, start.isoformat(), end.isoformat())
                if bars:
                    report["baostock_kline_success_count"] += 1
                else:
                    report["baostock_kline_failed_count"] += 1
                    _append_error(errors_sample, stock.code, "empty kline")
            except Exception as exc:
                report["baostock_kline_failed_count"] += 1
                _append_error(errors_sample, stock.code, exc)

    finished_at = datetime.now(timezone.utc)
    report["finished_at"] = finished_at.isoformat()
    report["duration_seconds"] = round((finished_at - started_at).total_seconds(), 3)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "summary: "
        f"akshare_available={report['akshare_available']} "
        f"akshare_universe_count={report['akshare_universe_count']} "
        f"baostock_available={report['baostock_available']} "
        f"baostock_universe_count={report['baostock_universe_count']} "
        f"baostock_kline_success_count={report['baostock_kline_success_count']} "
        f"baostock_kline_failed_count={report['baostock_kline_failed_count']} "
        f"report_path={output_path}"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test manual real data source providers.")
    parser.add_argument("--akshare", action="store_true")
    parser.add_argument("--baostock", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--output", default=str(REPORT_PATH))
    parser.add_argument("--no-proxy", action="store_true", help="Clear AKShare proxy env vars in this process.")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--max-lookback-days", type=int, default=15)
    args = parser.parse_args()
    run_smoke_real_data_sources(
        check_akshare=args.akshare or not args.baostock,
        check_baostock=args.baostock or not args.akshare,
        limit=args.limit,
        output=args.output,
        no_proxy=args.no_proxy,
        trade_date=args.trade_date,
        max_lookback_days=args.max_lookback_days,
    )
    return 0


def _append_error(errors: list[dict[str, Any]], scope: str, error: Any) -> None:
    if len(errors) >= 50:
        return
    errors.append({"scope": scope, "error_type": error.__class__.__name__, "message": str(error)})


def _akshare_suggestion(diagnostics: dict[str, Any]) -> str:
    if diagnostics.get("error_type") == "ProxyError":
        if diagnostics.get("proxy_mode") == "no_proxy":
            return "No-proxy mode still saw proxy-style failure; check system proxy/VPN or EastMoney route."
        if diagnostics.get("proxy_env_detected"):
            return "Proxy env vars detected; retry with --no-proxy or fix proxy settings."
        return "Proxy-like error detected; check local proxy/network settings."
    return "Check network diagnostics report and AKShare/EastMoney endpoint availability."


if __name__ == "__main__":
    raise SystemExit(main())
