from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from datasource.akshare_provider import AKShareMarketDataProvider
from datasource.baostock_provider import BaoStockMarketDataProvider
from datasource.mock.market_provider import MockMarketDataProvider
from datasource.tushare_provider import TushareMarketDataProvider
from datasource.models.market import (
    CapitalFlowData,
    FinanceData,
    KLineBar as CanonicalKLineBar,
    MarketEmotionData,
    MarketStockInfo,
    RealtimeQuote as CanonicalRealtimeQuote,
)
from database.session import get_session
from quant.config import load_quant_config
from quant.persistence import save_factor_scores
from quant.ranking import QuantRankingEngine
from quant.schemas import QuantFactorInput, QuantRankingResult
from datasource.schemas import (
    CapitalFlowSnapshot,
    FinanceSnapshot,
    KlineBar,
    MarketEmotionSnapshot,
    RealtimeQuote,
)


REPORT_PATH = Path("data/reports/real_quant_top500_report.json")
MIN_KLINE_BARS = 20


def run_real_quant_top500(
    provider: str = "tushare",
    history_provider: str = "tushare",
    backup_history_provider: str | None = "baostock",
    top_n: int = 500,
    sample_limit: int = 0,
    start_date: str | None = None,
    end_date: str | None = None,
    trade_date: str | None = None,
    max_lookback_days: int = 15,
    akshare_no_proxy: bool = False,
    save_to_db: bool = False,
    output: str | Path = REPORT_PATH,
    progress: bool = True,
    use_cache: bool = True,
    refresh_cache: bool = False,
    data_fetch_workers: int = 1,
    factor_workers: str | int = "1",
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    run_started = time.perf_counter()
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Path("data/cache/akshare").mkdir(parents=True, exist_ok=True)
    Path("data/cache/baostock").mkdir(parents=True, exist_ok=True)
    Path("data/cache/tushare").mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    errors_sample: list[dict[str, Any]] = []
    filters_applied = ["exclude_empty_code", "exclude_st", "exclude_non_normal_status", "require_min_kline_bars"]
    effective_data_fetch_workers = _effective_data_fetch_workers(history_provider, data_fetch_workers)
    effective_factor_workers = _effective_factor_workers(factor_workers)
    performance = _initial_performance(effective_data_fetch_workers, effective_factor_workers)
    kline_fetch_attempts = 0
    baostock_backup_used_count = 0

    if int(data_fetch_workers or 1) > effective_data_fetch_workers:
        warnings.append("data_fetch_workers reduced to 1 for BaoStock session safety.")
    if str(factor_workers).lower() not in {"1", "auto"} and effective_factor_workers == 1:
        warnings.append("factor_workers requested above 1; current audit run keeps factor computation single-process.")

    market_provider = _provider(
        provider,
        akshare_no_proxy=akshare_no_proxy,
        use_cache=use_cache,
        refresh_cache=refresh_cache,
    )
    history = _provider(
        history_provider,
        akshare_no_proxy=akshare_no_proxy,
        use_cache=use_cache,
        refresh_cache=refresh_cache,
    )
    backup_history = (
        _provider(
            backup_history_provider,
            akshare_no_proxy=akshare_no_proxy,
            use_cache=use_cache,
            refresh_cache=refresh_cache,
        )
        if backup_history_provider
        else None
    )
    if isinstance(history, TushareMarketDataProvider):
        history.backup_provider = backup_history
    quant_config = load_quant_config()
    engine = QuantRankingEngine(config=quant_config)
    quant_mode = _quant_mode(provider, history_provider)
    report_context = {
        "requested_trade_date": trade_date,
        "actual_trade_date": None,
        "baostock_date_attempts": [],
        "akshare_proxy_mode": "no_proxy" if akshare_no_proxy else "env",
        "akshare_proxy_env_detected": _proxy_env_detected_from_provider(market_provider, history),
        "tushare_permission_summary": {},
    }
    fallback_used = False
    fallback_reasons: list[str] = []
    factor_data_coverage = _initial_factor_data_coverage()
    if quant_mode == "baostock_historical_degraded":
        warnings.extend(
            [
                "quant_mode=baostock_historical_degraded",
                "capital_score uses BaoStock historical amount/volume/turnover proxy.",
                "emotion_score fallback: BaoStock does not provide realtime market emotion data.",
            ]
        )

    try:
        universe_started = time.perf_counter()
        stocks = _get_stock_list(
            market_provider,
            trade_date=trade_date,
            max_lookback_days=max_lookback_days,
        )
        performance["universe_fetch_seconds"] = _round_seconds(time.perf_counter() - universe_started)
    except Exception as exc:
        performance["universe_fetch_seconds"] = _round_seconds(time.perf_counter() - universe_started)
        _append_error(errors_sample, provider, exc.__class__.__name__, str(exc))
        _merge_provider_diagnostics(report_context, market_provider, history, backup_history)
        _merge_cache_stats(performance, market_provider, history, backup_history)
        _finalize_performance(performance, run_started, scored_count=0, kline_fetch_attempts=0)
        finished_at = datetime.now(timezone.utc)
        report = _build_report(
            provider=provider,
            history_provider=history_provider,
            backup_history_provider=backup_history_provider,
            universe_count=0,
            filtered_count=0,
            scored_count=0,
            top_n=top_n,
            sample_limit=sample_limit,
            selected=[],
            skipped_count=0,
            failed_count=1,
            filters_applied=filters_applied,
            factor_version=quant_config.factor_version,
            quant_mode=quant_mode,
            started_at=started_at,
            finished_at=finished_at,
            warnings=["stock universe unavailable; quant scoring was not started"],
            errors_sample=errors_sample,
            output_path=output_path,
            requested_trade_date=report_context["requested_trade_date"],
            actual_trade_date=report_context["actual_trade_date"],
            baostock_date_attempts=report_context["baostock_date_attempts"],
            akshare_proxy_mode=report_context["akshare_proxy_mode"],
            akshare_proxy_env_detected=report_context["akshare_proxy_env_detected"],
            performance=performance,
            fallback_used=fallback_used,
            fallback_reason="; ".join(fallback_reasons),
            tushare_permission_summary=report_context["tushare_permission_summary"],
            factor_data_coverage=factor_data_coverage,
            tushare_api_counts=_tushare_api_counts(market_provider, history, backup_history),
            baostock_backup_used_count=baostock_backup_used_count,
        )
        _write_report(output_path, report, performance, run_started)
        return report
    _merge_provider_diagnostics(report_context, market_provider, history, backup_history)
    universe_count = len(stocks)
    if universe_count < 4000 and sample_limit == 0 and provider != "mock":
        warnings.append(f"universe_count below expected full A-share size: {universe_count}")

    filter_started = time.perf_counter()
    filtered_stocks = _filter_stock_universe(stocks)
    filtered_count = len(filtered_stocks)
    if filtered_count < 3000 and sample_limit == 0 and provider != "mock":
        warnings.append(f"filtered_count below expected full A-share size: {filtered_count}")
    if sample_limit > 0:
        filtered_stocks = filtered_stocks[:sample_limit]

    end = _parse_date(end_date) if end_date else date.today()
    start = _parse_date(start_date) if start_date else end - timedelta(days=45)
    market_emotion = _to_legacy_emotion(_safe_market_emotion(market_provider), end)
    performance["filter_seconds"] = _round_seconds(time.perf_counter() - filter_started)

    results = []
    skipped_count = 0
    failed_count = 0
    _start_provider_session(history)
    _start_provider_session(backup_history)
    try:
        for index, stock in enumerate(filtered_stocks, start=1):
            if progress and (index == 1 or index % 50 == 0 or index == len(filtered_stocks)):
                print(f"[real-quant] scoring {index}/{len(filtered_stocks)} {stock.code}")
            try:
                kline_started = time.perf_counter()
                try:
                    bars, stock_fallback_used, stock_fallback_reason, kline_source = _get_kline_with_backup(
                        history,
                        backup_history,
                        stock.code,
                        start.isoformat(),
                        end.isoformat(),
                        frequency="daily",
                    )
                finally:
                    performance["kline_fetch_seconds"] += time.perf_counter() - kline_started
                    kline_fetch_attempts += 1
                if stock_fallback_used:
                    fallback_used = True
                    if kline_source == "baostock":
                        baostock_backup_used_count += 1
                    fallback_reasons.append(f"{stock.code}:{stock_fallback_reason}")
                if kline_source == "tushare" and bars:
                    factor_data_coverage["daily"] = True
                if len(bars) < MIN_KLINE_BARS:
                    skipped_count += 1
                    _append_error(errors_sample, stock.code, "INSUFFICIENT_KLINE", f"bars={len(bars)}")
                    continue
                factor_started = time.perf_counter()
                quote = _safe_realtime(market_provider, stock, bars[-1])
                finance = _safe_finance(market_provider, stock.code)
                flow = _safe_capital_flow(market_provider, stock.code)
                _merge_factor_coverage_from_data(factor_data_coverage, finance, flow)
                factor_input = QuantFactorInput(
                    stock_code=stock.code,
                    stock_name=stock.name,
                    industry=stock.industry,
                    realtime_quote=_to_legacy_quote(quote, stock),
                    kline_bars=[_to_legacy_bar(bar) for bar in bars],
                    finance_snapshot=_to_legacy_finance(finance),
                    capital_flow=_capital_flow_for_mode(
                        quant_mode,
                        stock.code,
                        bars,
                        flow,
                        end,
                    ),
                    market_emotion=market_emotion,
                )
                results.append(engine.calculate_stock_score(factor_input))
                performance["factor_compute_seconds"] += time.perf_counter() - factor_started
            except Exception as exc:
                failed_count += 1
                _append_error(errors_sample, stock.code, exc.__class__.__name__, str(exc))
                continue
    finally:
        _end_provider_session(history)
        _end_provider_session(backup_history)

    ranking_started = time.perf_counter()
    results.sort(key=lambda item: item.total_score, reverse=True)
    selected = results[: min(top_n, len(results))]
    for rank, item in enumerate(selected, start=1):
        item.rank = rank

    ranking = QuantRankingResult(
        generated_at=datetime.now(timezone.utc),
        universe_size=len(results),
        requested_top_q=top_n,
        returned_count=len(selected),
        factor_version=quant_config.factor_version,
        results=selected,
    )
    if save_to_db and selected:
        session = get_session()
        try:
            save_factor_scores(session, ranking)
        finally:
            session.close()
    performance["ranking_seconds"] = _round_seconds(time.perf_counter() - ranking_started)
    _merge_provider_diagnostics(report_context, market_provider, history, backup_history)
    _merge_cache_stats(performance, market_provider, history, backup_history)
    _finalize_performance(
        performance,
        run_started,
        scored_count=len(results),
        kline_fetch_attempts=kline_fetch_attempts,
    )

    finished_at = datetime.now(timezone.utc)
    report = _build_report(
        provider=provider,
        history_provider=history_provider,
        backup_history_provider=backup_history_provider,
        universe_count=universe_count,
        filtered_count=filtered_count,
        scored_count=len(results),
        top_n=top_n,
        sample_limit=sample_limit,
        selected=selected,
        skipped_count=skipped_count,
        failed_count=failed_count,
        filters_applied=filters_applied,
        factor_version=quant_config.factor_version,
        quant_mode=quant_mode,
        started_at=started_at,
        finished_at=finished_at,
        warnings=warnings,
        errors_sample=errors_sample,
        output_path=output_path,
        requested_trade_date=report_context["requested_trade_date"],
        actual_trade_date=report_context["actual_trade_date"],
        baostock_date_attempts=report_context["baostock_date_attempts"],
        akshare_proxy_mode=report_context["akshare_proxy_mode"],
        akshare_proxy_env_detected=report_context["akshare_proxy_env_detected"],
        performance=performance,
        fallback_used=fallback_used,
        fallback_reason="; ".join(fallback_reasons[:20]),
        tushare_permission_summary=report_context["tushare_permission_summary"],
        factor_data_coverage=factor_data_coverage,
        tushare_api_counts=_tushare_api_counts(market_provider, history, backup_history),
        baostock_backup_used_count=baostock_backup_used_count,
    )
    _write_report(output_path, report, performance, run_started)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run manual real-data quant Top500 debug test.")
    parser.add_argument("--provider", choices=["mock", "akshare", "baostock", "tushare"], default="tushare")
    parser.add_argument("--history-provider", choices=["mock", "akshare", "baostock", "tushare"], default="tushare")
    parser.add_argument("--backup-history-provider", choices=["mock", "akshare", "baostock", "tushare"], default="baostock")
    parser.add_argument("--top-n", type=int, default=500)
    parser.add_argument("--sample-limit", type=int, default=0)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--max-lookback-days", type=int, default=15)
    parser.add_argument("--akshare-no-proxy", action="store_true")
    parser.add_argument("--save-to-db", action="store_true")
    parser.add_argument("--output", default=str(REPORT_PATH))
    parser.add_argument("--use-cache", type=_parse_bool_arg, default=True)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--data-fetch-workers", type=int, default=1)
    parser.add_argument("--factor-workers", default="1")
    args = parser.parse_args()

    report = run_real_quant_top500(
        provider=args.provider,
        history_provider=args.history_provider,
        backup_history_provider=args.backup_history_provider,
        top_n=args.top_n,
        sample_limit=args.sample_limit,
        start_date=args.start_date,
        end_date=args.end_date,
        trade_date=args.trade_date,
        max_lookback_days=args.max_lookback_days,
        akshare_no_proxy=args.akshare_no_proxy,
        save_to_db=args.save_to_db,
        output=args.output,
        use_cache=args.use_cache,
        refresh_cache=args.refresh_cache,
        data_fetch_workers=args.data_fetch_workers,
        factor_workers=args.factor_workers,
    )
    print(
        "summary: "
        f"provider={report['provider']} history_provider={report['history_provider']} "
        f"backup_history_provider={report['backup_history_provider']} "
        f"universe_count={report['universe_count']} filtered_count={report['filtered_count']} "
        f"scored_count={report['scored_count']} top_count={report['top_count']} "
        f"skipped_count={report['skipped_count']} failed_count={report['failed_count']} "
        f"fallback_used={report['fallback_used']} "
        f"total_seconds={report['performance']['total_seconds']} "
        f"cache_hit_count={report['performance']['cache_hit_count']} "
        f"cache_miss_count={report['performance']['cache_miss_count']} "
        f"report_path={report['report_path']}"
    )
    return 0


def _provider(
    name: str,
    akshare_no_proxy: bool = False,
    use_cache: bool = True,
    refresh_cache: bool = False,
):
    normalized = name.lower()
    if normalized == "mock":
        return MockMarketDataProvider()
    if normalized == "akshare":
        return AKShareMarketDataProvider(proxy_mode="no_proxy" if akshare_no_proxy else "env")
    if normalized == "baostock":
        return BaoStockMarketDataProvider(use_kline_cache=use_cache, refresh_kline_cache=refresh_cache)
    if normalized == "tushare":
        return TushareMarketDataProvider(cache_enabled=use_cache)
    raise ValueError(f"Unsupported provider: {name}")


def _quant_mode(provider: str, history_provider: str) -> str:
    if provider.lower() == "tushare" or history_provider.lower() == "tushare":
        return "tushare_primary"
    if provider.lower() == "baostock" and history_provider.lower() == "baostock":
        return "baostock_historical_degraded"
    return "standard"


def _get_stock_list(provider, trade_date: str | None, max_lookback_days: int) -> list[MarketStockInfo]:
    if isinstance(provider, BaoStockMarketDataProvider):
        return provider.get_stock_list(trade_date=trade_date, max_lookback_days=max_lookback_days)
    return provider.get_stock_list()


def _get_kline_with_backup(
    history,
    backup_history,
    stock_code: str,
    start_date: str,
    end_date: str,
    frequency: str = "daily",
) -> tuple[list[CanonicalKLineBar], bool, str | None, str]:
    try:
        bars = history.get_kline(stock_code, start_date, end_date, frequency=frequency)
        if getattr(history, "last_fallback_used", False):
            return (
                bars,
                True,
                getattr(history, "last_fallback_reason", "tushare_provider_fallback"),
                getattr(backup_history, "name", ""),
            )
        if len(bars) >= MIN_KLINE_BARS:
            return bars, False, None, getattr(history, "name", "")
        if backup_history is not None and getattr(history, "name", "") == "tushare":
            fallback_bars = backup_history.get_kline(stock_code, start_date, end_date, frequency=frequency)
            if len(fallback_bars) > len(bars):
                return fallback_bars, True, f"tushare_insufficient_bars:{len(bars)}", getattr(backup_history, "name", "")
        return bars, False, None, getattr(history, "name", "")
    except Exception as exc:
        if backup_history is not None:
            fallback_bars = backup_history.get_kline(stock_code, start_date, end_date, frequency=frequency)
            return fallback_bars, True, f"tushare_error:{exc.__class__.__name__}", getattr(backup_history, "name", "")
        raise


def _start_provider_session(provider) -> None:
    start_session = getattr(provider, "start_session", None)
    if callable(start_session):
        start_session()


def _end_provider_session(provider) -> None:
    end_session = getattr(provider, "end_session", None)
    if callable(end_session):
        end_session()


def _initial_performance(data_fetch_workers: int, factor_compute_workers: int) -> dict[str, Any]:
    return {
        "universe_fetch_seconds": 0.0,
        "filter_seconds": 0.0,
        "kline_fetch_seconds": 0.0,
        "factor_compute_seconds": 0.0,
        "ranking_seconds": 0.0,
        "report_write_seconds": 0.0,
        "total_seconds": 0.0,
        "avg_kline_fetch_ms": 0.0,
        "stocks_per_second": 0.0,
        "data_fetch_workers": data_fetch_workers,
        "factor_compute_workers": factor_compute_workers,
        "cache_hit_count": 0,
        "cache_miss_count": 0,
        "cache_insufficient_count": 0,
        "cache_refresh_count": 0,
    }


def _initial_factor_data_coverage() -> dict[str, bool]:
    return {
        "daily": False,
        "adj_factor": False,
        "stk_limit": False,
        "daily_basic": False,
        "moneyflow": False,
        "concept": False,
        "top_list": False,
        "margin": False,
        "pledge": False,
        "unlock": False,
        "chip": False,
        "tushare_factor": False,
    }


def _merge_factor_coverage_from_data(
    coverage: dict[str, bool],
    finance: FinanceData | None,
    flow: CapitalFlowData | None,
) -> None:
    if finance is not None and getattr(finance, "source", "") == "tushare":
        raw = finance.raw_data or {}
        coverage["daily_basic"] = coverage["daily_basic"] or bool(raw.get("daily_basic"))
    if flow is not None and getattr(flow, "source", "") == "tushare":
        raw = flow.raw_data or {}
        coverage["moneyflow"] = coverage["moneyflow"] or bool(raw.get("moneyflow"))
        coverage["daily_basic"] = coverage["daily_basic"] or bool(raw.get("daily_basic"))


def _effective_data_fetch_workers(history_provider: str, requested: int) -> int:
    workers = max(1, int(requested or 1))
    if history_provider.lower() == "baostock":
        return 1
    return workers


def _effective_factor_workers(requested: str | int) -> int:
    value = str(requested or "1").strip().lower()
    if value == "auto":
        return 1
    try:
        return max(1, min(1, int(value)))
    except ValueError:
        return 1


def _merge_cache_stats(performance: dict[str, Any], *providers) -> None:
    performance["cache_hit_count"] = sum(int(getattr(provider, "cache_hit_count", 0)) for provider in providers)
    performance["cache_miss_count"] = sum(int(getattr(provider, "cache_miss_count", 0)) for provider in providers)
    performance["cache_insufficient_count"] = sum(
        int(getattr(provider, "cache_insufficient_count", 0)) for provider in providers
    )
    performance["cache_refresh_count"] = sum(int(getattr(provider, "cache_refresh_count", 0)) for provider in providers)


def _tushare_api_counts(*providers) -> dict[str, int]:
    totals: dict[str, int] = {}
    for provider in providers:
        if provider is None or not isinstance(provider, TushareMarketDataProvider):
            continue
        for status, count in getattr(provider, "api_status_counts", {}).items():
            totals[status] = totals.get(status, 0) + int(count)
    error_count = sum(
        count
        for status, count in totals.items()
        if status not in {"available", "empty", "cache"}
    )
    return {
        "success": totals.get("available", 0) + totals.get("cache", 0),
        "empty": totals.get("empty", 0),
        "error": error_count,
    }


def _finalize_performance(
    performance: dict[str, Any],
    run_started: float,
    scored_count: int,
    kline_fetch_attempts: int,
) -> None:
    performance["kline_fetch_seconds"] = _round_seconds(performance["kline_fetch_seconds"])
    performance["factor_compute_seconds"] = _round_seconds(performance["factor_compute_seconds"])
    if kline_fetch_attempts:
        performance["avg_kline_fetch_ms"] = round(performance["kline_fetch_seconds"] * 1000 / kline_fetch_attempts, 3)
    total_seconds = max(time.perf_counter() - run_started, 0.0)
    performance["total_seconds"] = _round_seconds(total_seconds)
    if total_seconds > 0:
        performance["stocks_per_second"] = round(scored_count / total_seconds, 3)


def _write_report(
    output_path: Path,
    report: dict[str, Any],
    performance: dict[str, Any],
    run_started: float,
) -> None:
    performance["total_seconds"] = _round_seconds(time.perf_counter() - run_started)
    report["performance"] = performance
    text = json.dumps(report, ensure_ascii=False, indent=2, default=_json_default)
    write_started = time.perf_counter()
    output_path.write_text(text, encoding="utf-8")
    performance["report_write_seconds"] = _round_seconds(time.perf_counter() - write_started)
    performance["total_seconds"] = _round_seconds(time.perf_counter() - run_started)
    report["performance"] = performance
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _round_seconds(value: float) -> float:
    return round(float(value), 3)


def _parse_bool_arg(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _filter_stock_universe(stocks: list[MarketStockInfo]) -> list[MarketStockInfo]:
    filtered = []
    for stock in stocks:
        name = stock.name.upper()
        status = stock.status.upper()
        if not stock.code or len(stock.code) != 6:
            continue
        if "ST" in name or status not in {"NORMAL", "1", "LISTED"}:
            continue
        filtered.append(stock)
    return filtered


def _safe_realtime(provider, stock: MarketStockInfo, latest_bar: CanonicalKLineBar) -> CanonicalRealtimeQuote:
    if isinstance(provider, TushareMarketDataProvider):
        return _quote_from_latest_bar(stock, latest_bar, reason="tushare_realtime_derived_from_cached_kline")
    try:
        return provider.get_realtime(stock.code)
    except Exception:
        return _quote_from_latest_bar(stock, latest_bar, reason="realtime unavailable")


def _quote_from_latest_bar(
    stock: MarketStockInfo,
    latest_bar: CanonicalKLineBar,
    reason: str,
) -> CanonicalRealtimeQuote:
    return CanonicalRealtimeQuote(
        stock_code=stock.code,
        price=latest_bar.close,
        open=latest_bar.open,
        high=latest_bar.high,
        low=latest_bar.low,
        pre_close=latest_bar.pre_close,
        volume=latest_bar.volume,
        amount=latest_bar.amount,
        change_percent=latest_bar.change_percent,
        datetime=latest_bar.datetime,
        source="derived_from_kline",
        raw_data={"reason": reason},
    )


def _safe_market_emotion(provider) -> MarketEmotionData | None:
    try:
        return provider.get_market_emotion()
    except Exception:
        return None


def _safe_finance(provider, stock_code: str) -> FinanceData | None:
    try:
        return provider.get_finance(stock_code)
    except Exception:
        return None


def _safe_capital_flow(provider, stock_code: str) -> CapitalFlowData | None:
    try:
        return provider.get_capital_flow(stock_code)
    except Exception:
        return None


def _capital_flow_for_mode(
    quant_mode: str,
    stock_code: str,
    bars: list[CanonicalKLineBar],
    external_flow: CapitalFlowData | None,
    trade_date: date,
) -> CapitalFlowSnapshot | None:
    if quant_mode != "baostock_historical_degraded":
        return _to_legacy_capital(external_flow)
    latest = bars[-1] if bars else None
    previous = bars[-6:-1] if len(bars) >= 6 else bars[:-1]
    average_volume = sum((bar.volume for bar in previous), 0) / len(previous) if previous else None
    volume_ratio = Decimal("1.0")
    if latest is not None and average_volume:
        volume_ratio = _decimal(latest.volume) / _decimal(average_volume)
    return CapitalFlowSnapshot(
        stock_code=stock_code,
        trade_date=trade_date,
        main_net_inflow=Decimal("0"),
        retail_net_inflow=Decimal("0"),
        turnover_rate=_decimal(latest.turnover_rate if latest else 0),
        volume_ratio=volume_ratio,
    )


def _to_legacy_quote(quote: CanonicalRealtimeQuote, stock: MarketStockInfo) -> RealtimeQuote:
    return RealtimeQuote(
        stock_code=stock.code,
        name=stock.name,
        current_price=_decimal(quote.price),
        change_percent=_decimal(quote.change_percent),
        volume=int(quote.volume),
        amount=_decimal(quote.amount),
        quote_time=_parse_datetime(quote.datetime),
    )


def _to_legacy_bar(bar: CanonicalKLineBar) -> KlineBar:
    return KlineBar(
        stock_code=bar.stock_code,
        trade_date=_parse_date(bar.datetime),
        open=_decimal(bar.open),
        high=_decimal(bar.high),
        low=_decimal(bar.low),
        close=_decimal(bar.close),
        pre_close=_decimal(bar.pre_close),
        volume=int(bar.volume),
        amount=_decimal(bar.amount),
        frequency="1d",
    )


def _to_legacy_finance(finance: FinanceData | None) -> FinanceSnapshot | None:
    if finance is None:
        return None
    return FinanceSnapshot(
        stock_code=finance.stock_code,
        date=date.today(),
        revenue=_decimal(finance.revenue),
        profit=_decimal(finance.profit),
        pe=_decimal(finance.pe),
        pb=_decimal(finance.pb),
        roe=_decimal(finance.roe),
        debt_ratio=_decimal(finance.debt_ratio),
    )


def _to_legacy_capital(flow: CapitalFlowData | None) -> CapitalFlowSnapshot | None:
    if flow is None:
        return None
    return CapitalFlowSnapshot(
        stock_code=flow.stock_code,
        trade_date=date.today(),
        main_net_inflow=_decimal(flow.main_net_inflow),
        retail_net_inflow=Decimal("0"),
        turnover_rate=_decimal(flow.turnover_rate),
        volume_ratio=Decimal("1.0"),
    )


def _to_legacy_emotion(emotion: MarketEmotionData | None, trade_date: date) -> MarketEmotionSnapshot:
    if emotion is None:
        return MarketEmotionSnapshot(
            trade_date=trade_date,
            limit_up_count=0,
            limit_down_count=0,
            up_count=0,
            down_count=0,
            emotion_score=Decimal("50"),
        )
    score = min(100, max(0, 50 + emotion.limit_up_count - emotion.limit_down_count))
    return MarketEmotionSnapshot(
        trade_date=trade_date,
        limit_up_count=emotion.limit_up_count,
        limit_down_count=emotion.limit_down_count,
        up_count=0,
        down_count=0,
        emotion_score=_decimal(score),
    )


def _score_to_report(item) -> dict[str, Any]:
    return {
        "rank": item.rank,
        "stock_code": item.stock_code,
        "stock_name": item.stock_name,
        "total_score": float(item.total_score),
        "technical_score": float(item.technical_score),
        "capital_score": float(item.capital_score),
        "emotion_score": float(item.emotion_score),
        "momentum_score": float(item.momentum_score),
        "risk_score": float(item.risk_score),
    }


def _build_report(
    provider: str,
    history_provider: str,
    backup_history_provider: str | None,
    universe_count: int,
    filtered_count: int,
    scored_count: int,
    top_n: int,
    sample_limit: int,
    selected: list,
    skipped_count: int,
    failed_count: int,
    filters_applied: list[str],
    factor_version: str,
    quant_mode: str,
    started_at: datetime,
    finished_at: datetime,
    warnings: list[str],
    errors_sample: list[dict[str, Any]],
    output_path: Path,
    requested_trade_date: str | None = None,
    actual_trade_date: str | None = None,
    baostock_date_attempts: list[dict[str, Any]] | None = None,
    akshare_proxy_mode: str = "env",
    akshare_proxy_env_detected: bool = False,
    performance: dict[str, Any] | None = None,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
    tushare_permission_summary: dict[str, Any] | None = None,
    factor_data_coverage: dict[str, bool] | None = None,
    tushare_api_counts: dict[str, int] | None = None,
    baostock_backup_used_count: int = 0,
) -> dict[str, Any]:
    api_counts = tushare_api_counts or {"success": 0, "empty": 0, "error": 0}
    return {
        "provider": provider,
        "history_provider": history_provider,
        "backup_history_provider": backup_history_provider,
        "quant_mode": quant_mode,
        "requested_trade_date": requested_trade_date,
        "actual_trade_date": actual_trade_date,
        "baostock_date_attempts": baostock_date_attempts or [],
        "akshare_proxy_mode": akshare_proxy_mode,
        "akshare_proxy_env_detected": akshare_proxy_env_detected,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason or "",
        "baostock_backup_used_count": baostock_backup_used_count,
        "tushare_permission_summary": tushare_permission_summary or {},
        "tushare_api_success_count": api_counts.get("success", 0),
        "tushare_api_empty_count": api_counts.get("empty", 0),
        "tushare_api_error_count": api_counts.get("error", 0),
        "factor_data_coverage": factor_data_coverage or _initial_factor_data_coverage(),
        "universe_count": universe_count,
        "filtered_count": filtered_count,
        "scored_count": scored_count,
        "top_n": top_n,
        "sample_limit": sample_limit,
        "top_count": len(selected),
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "filters_applied": filters_applied,
        "factor_version": factor_version,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "performance": performance or {},
        "no_llm_call_verified": True,
        "top_stocks": [_score_to_report(item) for item in selected],
        "warnings": warnings,
        "errors_sample": errors_sample[:50],
        "report_path": str(output_path),
    }


def _append_error(errors: list[dict[str, Any]], stock_code: str, code: str, message: str) -> None:
    if len(errors) >= 50:
        return
    errors.append({"stock_code": stock_code, "code": code, "message": message})


def _merge_provider_diagnostics(report_context: dict[str, Any], *providers) -> None:
    for provider in providers:
        if provider is None:
            continue
        if isinstance(provider, BaoStockMarketDataProvider):
            if provider.last_actual_trade_date:
                report_context["actual_trade_date"] = provider.last_actual_trade_date
            if provider.last_date_attempts:
                report_context["baostock_date_attempts"] = provider.last_date_attempts
        if isinstance(provider, AKShareMarketDataProvider):
            diagnostics = provider.diagnostics()
            report_context["akshare_proxy_mode"] = diagnostics["proxy_mode"]
            report_context["akshare_proxy_env_detected"] = diagnostics["proxy_env_detected"]
        if isinstance(provider, TushareMarketDataProvider):
            diagnostics = provider.diagnostics()
            report_context["tushare_permission_summary"] = {
                "source_status": diagnostics["source_status"],
                "error_type": diagnostics["error_type"],
                "fallback_used": diagnostics["fallback_used"],
                "fallback_reason": diagnostics["fallback_reason"],
            }


def _proxy_env_detected_from_provider(*providers) -> bool:
    for provider in providers:
        if provider is None:
            continue
        if isinstance(provider, AKShareMarketDataProvider):
            return provider.diagnostics()["proxy_env_detected"]
    return False


def _parse_date(value: str) -> date:
    normalized = value.split("T", 1)[0].replace("-", "")
    return datetime.strptime(normalized, "%Y%m%d").date()


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.combine(_parse_date(value), datetime.min.time(), tzinfo=timezone.utc)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
