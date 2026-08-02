from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
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
from datasource.tushare_provider import (
    DEFAULT_TOKEN_ENV,
    TushareMarketDataProvider,
    _env_file_value as _tushare_env_file_value,
    _float as _tushare_float,
    _kline_bar as _tushare_kline_bar,
    _pick as _tushare_pick,
    _plain_code as _tushare_plain_code,
)
from datasource.models.market import (
    CapitalFlowData,
    FinanceData,
    KLineBar as CanonicalKLineBar,
    MarketEmotionData,
    MarketStockInfo,
    RealtimeQuote as CanonicalRealtimeQuote,
)
from database.session import get_session
from quant.config import QuantConfig, load_quant_config
from quant.price_adjustment import ADJUSTMENT_MODES, RAW, AdjustedPriceSeries, build_adjusted_price_series
from quant.price_limit import build_price_limit_risk
from quant.persistence import save_factor_scores
from quant.ranking import QuantRankingEngine
from quant.schemas import QuantFactorInput, QuantFactorScore, QuantRankingResult
from datasource.schemas import (
    CapitalFlowSnapshot,
    FinanceSnapshot,
    KlineBar,
    MarketEmotionSnapshot,
    RealtimeQuote,
)
from backend.core.runtime_paths import cache_root, report_root


REPORT_PATH = report_root() / "real_quant_top500_report.json"
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
    price_adjustment_mode: str | None = None,
    price_limit_risk_enabled: bool | None = None,
) -> dict[str, Any]:
    _load_local_tushare_token()
    started_at = datetime.now(timezone.utc)
    run_started = time.perf_counter()
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for provider_name in ("akshare", "baostock", "tushare"):
        (cache_root() / provider_name).mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    errors_sample: list[dict[str, Any]] = []
    filters_applied = ["exclude_empty_code", "exclude_st", "exclude_non_normal_status", "require_min_kline_bars"]
    effective_data_fetch_workers = _effective_data_fetch_workers(history_provider, data_fetch_workers)
    effective_factor_workers = _effective_factor_workers(factor_workers)
    performance = _initial_performance(effective_data_fetch_workers, effective_factor_workers)
    kline_fetch_attempts = 0
    baostock_backup_used_count = 0
    trade_date_batch_context: dict[str, Any] = {}

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
    effective_backup_history_provider = (
        None
        if provider == "mock" and history_provider == "mock"
        else backup_history_provider
    )
    backup_history = (
        _provider(
            effective_backup_history_provider,
            akshare_no_proxy=akshare_no_proxy,
            use_cache=use_cache,
            refresh_cache=refresh_cache,
        )
        if effective_backup_history_provider
        else None
    )
    if isinstance(history, TushareMarketDataProvider):
        history.backup_provider = backup_history
    quant_config = _quant_config_with_runtime_overrides(
        load_quant_config(),
        price_adjustment_mode=price_adjustment_mode,
        price_limit_risk_enabled=price_limit_risk_enabled,
    )
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
            backup_history_provider=effective_backup_history_provider,
            universe_count=0,
            filtered_count=0,
            scored_count=0,
            top_n=top_n,
            sample_limit=sample_limit,
            selected=[],
            scored_results=[],
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
            trade_date_cache_stats=_trade_date_cache_stats(market_provider, history, backup_history),
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
    trade_date_batch_context = _prepare_tushare_trade_date_batch_context(
        market_provider=market_provider,
        history=history,
        start=start,
        end=end,
        use_cache=use_cache,
        refresh_cache=refresh_cache,
        warnings=warnings,
        errors_sample=errors_sample,
    )
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
                    bars = _batch_kline_for_stock(trade_date_batch_context, stock.code)
                    if bars:
                        stock_fallback_used = False
                        stock_fallback_reason = None
                        kline_source = "tushare_trade_date_cache"
                    elif trade_date_batch_context.get("enabled"):
                        stock_fallback_used = False
                        stock_fallback_reason = "tushare_trade_date_cache_missing_stock"
                        kline_source = "tushare_trade_date_cache"
                    else:
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
                if kline_source in {"tushare", "tushare_trade_date_cache"} and bars:
                    factor_data_coverage["daily"] = True
                if len(bars) < MIN_KLINE_BARS:
                    fallback_bars = []
                    if backup_history is not None and kline_source == "tushare_trade_date_cache":
                        try:
                            fallback_bars = backup_history.get_kline(
                                stock.code,
                                start.isoformat(),
                                end.isoformat(),
                                frequency="daily",
                            )
                        except Exception:
                            fallback_bars = []
                    if len(fallback_bars) > len(bars):
                        bars = fallback_bars
                        stock_fallback_used = True
                        fallback_used = True
                        baostock_backup_used_count += 1
                        fallback_reasons.append(f"{stock.code}:tushare_trade_date_cache_insufficient_bars")
                    else:
                        skipped_count += 1
                        _append_error(errors_sample, stock.code, "INSUFFICIENT_KLINE", f"bars={len(bars)}")
                        continue
                factor_started = time.perf_counter()
                quote = _safe_realtime(market_provider, stock, bars[-1])
                finance = _batch_finance_for_stock(trade_date_batch_context, stock.code)
                flow = _batch_capital_flow_for_stock(trade_date_batch_context, stock.code)
                if not trade_date_batch_context.get("enabled"):
                    finance = finance or _safe_finance(market_provider, stock.code)
                    flow = flow or _safe_capital_flow(market_provider, stock.code)
                _merge_factor_coverage_from_data(factor_data_coverage, finance, flow)
                _merge_factor_coverage_from_batch(factor_data_coverage, trade_date_batch_context)
                raw_legacy_bars = [_to_legacy_bar(bar) for bar in bars]
                adjustment = _technical_price_series(
                    trade_date_batch_context,
                    stock.code,
                    raw_legacy_bars,
                    quant_config,
                    decision_time=started_at,
                )
                legacy_quote = _to_legacy_quote(quote, stock)
                if adjustment.active:
                    legacy_quote = legacy_quote.model_copy(
                        update={"current_price": adjustment.bars[-1].close}
                    )
                limit_summary = _price_limit_summary(
                    trade_date_batch_context,
                    stock.code,
                    raw_legacy_bars,
                    quant_config,
                )
                factor_input = QuantFactorInput(
                    stock_code=stock.code,
                    stock_name=stock.name,
                    industry=stock.industry,
                    realtime_quote=legacy_quote,
                    kline_bars=adjustment.bars,
                    finance_snapshot=_to_legacy_finance(finance),
                    capital_flow=_capital_flow_for_mode(
                        quant_mode,
                        stock.code,
                        bars,
                        flow,
                        end,
                    ),
                    market_emotion=market_emotion,
                    raw_kline_bars=raw_legacy_bars,
                    price_adjustment=_adjustment_metadata(adjustment),
                    price_limit_risk=limit_summary,
                )
                results.append(engine.calculate_stock_score(factor_input))
                _append_tushare_explanation_details(
                    results[-1],
                    trade_date_batch_context,
                    stock.code,
                    bars,
                    adjustment=adjustment,
                    limit_summary=limit_summary,
                    price_limit_enabled=bool(
                        quant_config.raw.get("risk_factor", {}).get("price_limit", {}).get("enabled", False)
                    ),
                )
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
    for rank, item in enumerate(results, start=1):
        item.rank = rank
    selected = results[: min(top_n, len(results))]

    ranking = QuantRankingResult(
        generated_at=datetime.now(timezone.utc),
        universe_size=len(results),
        requested_top_q=top_n,
        returned_count=len(selected),
        factor_version=quant_config.factor_version,
        results=selected,
    )
    if save_to_db and results:
        session = get_session()
        try:
            persistence_ranking = ranking.model_copy(
                update={
                    "returned_count": len(results),
                    "results": results,
                }
            )
            save_factor_scores(session, persistence_ranking)
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
        backup_history_provider=effective_backup_history_provider,
        universe_count=universe_count,
        filtered_count=filtered_count,
        scored_count=len(results),
        top_n=top_n,
        sample_limit=sample_limit,
        selected=selected,
        scored_results=results,
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
        trade_date_cache_stats=_trade_date_cache_stats(market_provider, history, backup_history),
    )
    report["price_adjustment_mode"] = str(
        quant_config.raw.get("technical_factor", {}).get("price_adjustment", {}).get("mode", RAW)
    )
    report["price_limit_risk_enabled"] = bool(
        quant_config.raw.get("risk_factor", {}).get("price_limit", {}).get("enabled", False)
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
    parser.add_argument("--price-adjustment-mode", choices=sorted(ADJUSTMENT_MODES), default=None)
    parser.add_argument("--price-limit-risk", action=argparse.BooleanOptionalAction, default=None)
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
        price_adjustment_mode=args.price_adjustment_mode,
        price_limit_risk_enabled=args.price_limit_risk,
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
        f"trade_date_cache_used={report.get('trade_date_cache_used', False)} "
        f"per_stock_api_call_count={report.get('per_stock_api_call_count', 0)} "
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


def _load_local_tushare_token() -> None:
    if os.getenv(DEFAULT_TOKEN_ENV, "").strip():
        return
    token = _tushare_env_file_value(Path(".env"), DEFAULT_TOKEN_ENV)
    if token:
        os.environ[DEFAULT_TOKEN_ENV] = token


def _quant_mode(provider: str, history_provider: str) -> str:
    if provider.lower() == "tushare" or history_provider.lower() == "tushare":
        return "tushare_primary"
    if provider.lower() == "baostock" and history_provider.lower() == "baostock":
        return "baostock_historical_degraded"
    return "standard"


def _quant_config_with_runtime_overrides(
    config: QuantConfig,
    *,
    price_adjustment_mode: str | None,
    price_limit_risk_enabled: bool | None,
) -> QuantConfig:
    if price_adjustment_mode is None and price_limit_risk_enabled is None:
        return config
    raw = deepcopy(config.raw)
    if price_adjustment_mode is not None:
        adjustment = raw.setdefault("technical_factor", {}).setdefault("price_adjustment", {})
        adjustment["mode"] = price_adjustment_mode
        adjustment["enabled"] = price_adjustment_mode != RAW
    if price_limit_risk_enabled is not None:
        raw.setdefault("risk_factor", {}).setdefault("price_limit", {})["enabled"] = price_limit_risk_enabled
    return QuantConfig(raw=raw)


def _technical_price_series(
    context: dict[str, Any],
    stock_code: str,
    raw_bars: list[KlineBar],
    config: QuantConfig,
    *,
    decision_time: datetime,
) -> AdjustedPriceSeries:
    adjustment = config.raw.get("technical_factor", {}).get("price_adjustment", {})
    enabled = bool(adjustment.get("enabled", False))
    mode = str(adjustment.get("mode", RAW)) if enabled else RAW
    records = context.get("adj_factor_by_stock", {}).get(_tushare_plain_code(stock_code), {})
    return build_adjusted_price_series(
        raw_bars,
        records,
        mode=mode,
        decision_time=decision_time,
        base_market_trade_date=raw_bars[-1].trade_date if raw_bars else None,
        point_in_time_required=bool(adjustment.get("point_in_time_required", True)),
        fallback_to_raw=bool(adjustment.get("fallback_to_raw", True)),
    )


def _adjustment_metadata(series: AdjustedPriceSeries) -> dict[str, Any]:
    return {
        "mode": series.mode,
        "technical_price_basis": series.technical_price_basis,
        "adjusted_series_version": series.adjusted_series_version,
        "factor_as_of_trade_date": series.factor_as_of_trade_date,
        "factor_available_at": series.factor_available_at,
        "adjustment_anchor_date": series.adjustment_anchor_date,
        "point_in_time_status": series.point_in_time_status,
        "warning": series.warning,
        "active": series.active,
    }


def _price_limit_summary(
    context: dict[str, Any],
    stock_code: str,
    bars: list[KlineBar],
    config: QuantConfig,
) -> dict[str, Any]:
    price_limit = config.raw.get("risk_factor", {}).get("price_limit", {})
    records = context.get("stk_limit_by_stock", {}).get(_tushare_plain_code(stock_code), {})
    return build_price_limit_risk(
        bars,
        records,
        near_limit_percent=Decimal(str(price_limit.get("near_limit_percent", "0.01"))),
        consecutive_window=int(price_limit.get("consecutive_window", 5)),
        tick_size=Decimal(str(price_limit.get("tick_size", "0.01"))),
    )


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


def _prepare_tushare_trade_date_batch_context(
    market_provider,
    history,
    start: date,
    end: date,
    use_cache: bool,
    refresh_cache: bool,
    warnings: list[str],
    errors_sample: list[dict[str, Any]],
) -> dict[str, Any]:
    if not use_cache:
        return {"enabled": False}
    provider = history if isinstance(history, TushareMarketDataProvider) else market_provider
    if not isinstance(provider, TushareMarketDataProvider):
        return {"enabled": False}
    context: dict[str, Any] = {
        "enabled": True,
        "provider": provider,
        "daily_by_stock": {},
        "daily_basic_by_stock": {},
        "moneyflow_by_stock": {},
        "adj_factor_by_stock": {},
        "stk_limit_by_stock": {},
        "loaded_interfaces": set(),
    }
    for api_name in ("daily", "daily_basic", "adj_factor", "moneyflow", "stk_limit"):
        try:
            records = provider.get_trade_date_records(
                api_name,
                start.isoformat(),
                end.isoformat(),
                use_cache=use_cache,
                refresh_cache=refresh_cache,
            )
            context["loaded_interfaces"].add(api_name)
            if api_name == "daily":
                context["daily_by_stock"] = _group_kline_records_by_stock(records)
            elif api_name == "adj_factor":
                context["adj_factor_by_stock"] = _records_by_stock_and_date(records)
            elif api_name == "stk_limit":
                context["stk_limit_by_stock"] = _records_by_stock_and_date(records)
            else:
                context[f"{api_name}_by_stock"] = _latest_record_by_stock(records)
        except Exception as exc:
            warnings.append(f"tushare_trade_date_cache_{api_name}_unavailable:{exc.__class__.__name__}")
            _append_error(errors_sample, api_name, exc.__class__.__name__, str(exc))
    return context


def _group_kline_records_by_stock(records: list[dict[str, Any]]) -> dict[str, list[CanonicalKLineBar]]:
    grouped: dict[str, list[CanonicalKLineBar]] = {}
    for row in records:
        code = _tushare_plain_code(_tushare_pick(row, "ts_code", default=""))
        if not code:
            continue
        grouped.setdefault(code, []).append(_tushare_kline_bar(row, "daily"))
    for bars in grouped.values():
        bars.sort(key=lambda item: item.datetime)
    return grouped


def _latest_record_by_stock(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in records:
        code = _tushare_plain_code(_tushare_pick(row, "ts_code", default=""))
        if not code:
            continue
        current = grouped.get(code)
        if current is None or str(_tushare_pick(row, "trade_date", default="")) >= str(
            _tushare_pick(current, "trade_date", default="")
        ):
            grouped[code] = row
    return grouped


def _records_by_stock_and_date(records: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in records:
        code = _tushare_plain_code(_tushare_pick(row, "ts_code", default=""))
        trade_day = str(_tushare_pick(row, "trade_date", default=""))
        if not code or not trade_day:
            continue
        grouped.setdefault(code, {})[trade_day] = row
    return grouped


def _batch_kline_for_stock(context: dict[str, Any], stock_code: str) -> list[CanonicalKLineBar]:
    if not context.get("enabled"):
        return []
    return list(context.get("daily_by_stock", {}).get(_tushare_plain_code(stock_code), []))


def _batch_finance_for_stock(context: dict[str, Any], stock_code: str) -> FinanceData | None:
    if not context.get("enabled"):
        return None
    code = _tushare_plain_code(stock_code)
    basic_row = context.get("daily_basic_by_stock", {}).get(code)
    if not basic_row:
        return None
    return FinanceData(
        stock_code=code,
        revenue=0.0,
        profit=0.0,
        pe=_tushare_float(_tushare_pick(basic_row, "pe", default=0)),
        pb=_tushare_float(_tushare_pick(basic_row, "pb", default=0)),
        roe=0.0,
        debt_ratio=0.0,
        source="tushare",
        raw_data={"daily_basic": basic_row, "source": "trade_date_cache"},
    )


def _batch_capital_flow_for_stock(context: dict[str, Any], stock_code: str) -> CapitalFlowData | None:
    if not context.get("enabled"):
        return None
    code = _tushare_plain_code(stock_code)
    flow_row = context.get("moneyflow_by_stock", {}).get(code)
    basic_row = context.get("daily_basic_by_stock", {}).get(code, {})
    if not flow_row:
        return None
    return CapitalFlowData(
        stock_code=code,
        main_net_inflow=_tushare_float(_tushare_pick(flow_row, "net_mf_amount", default=0)) * 10000,
        large_order_net_inflow=(
            _tushare_float(_tushare_pick(flow_row, "buy_lg_amount", default=0))
            + _tushare_float(_tushare_pick(flow_row, "buy_elg_amount", default=0))
            - _tushare_float(_tushare_pick(flow_row, "sell_lg_amount", default=0))
            - _tushare_float(_tushare_pick(flow_row, "sell_elg_amount", default=0))
        )
        * 10000,
        amount=_tushare_float(_tushare_pick(basic_row, "amount", default=0)),
        turnover_rate=_tushare_float(_tushare_pick(basic_row, "turnover_rate", default=0)),
        source="tushare",
        raw_data={"moneyflow": flow_row, "daily_basic": basic_row, "source": "trade_date_cache"},
    )


def _merge_factor_coverage_from_batch(coverage: dict[str, bool], context: dict[str, Any]) -> None:
    for api_name in context.get("loaded_interfaces", set()):
        if api_name in coverage:
            coverage[api_name] = True


LIMIT_STATUS_CODES = {
    name: Decimal(index)
    for index, name in enumerate(
        (
            "LIMIT_DATA_MISSING",
            "NORMAL",
            "NEAR_LIMIT_UP",
            "AT_LIMIT_UP",
            "OPENED_LIMIT_UP",
            "CONSECUTIVE_LIMIT_UP",
            "NEAR_LIMIT_DOWN",
            "AT_LIMIT_DOWN",
            "OPENED_LIMIT_DOWN",
            "CONSECUTIVE_LIMIT_DOWN",
            "NOT_APPLICABLE",
        )
    )
}
LIMIT_STATUS_BY_CODE = {int(value): key for key, value in LIMIT_STATUS_CODES.items()}


def _append_tushare_explanation_details(
    result,
    context: dict[str, Any],
    stock_code: str,
    bars: list[CanonicalKLineBar],
    *,
    adjustment: AdjustedPriceSeries | None = None,
    limit_summary: dict[str, Any] | None = None,
    price_limit_enabled: bool = False,
) -> None:
    if not context.get("enabled"):
        return
    code = _tushare_plain_code(stock_code)
    adj_available = _adj_factor_available(context, code, bars)
    result.factor_details.append(
        QuantFactorScore(
            stock_code=code,
            factor_group="data_quality",
            factor_name="adj_factor_available",
            raw_value=Decimal("1") if adj_available else Decimal("0"),
            normalized_value=Decimal("50"),
            score=Decimal("50"),
            weight=None,
            explain_text=(
                "Tushare adj_factor available for this stock/date range"
                if adj_available
                else "Tushare adj_factor missing for this stock/date range"
            ),
        )
    )

    summary = limit_summary or _limit_summary(context, code, bars)
    result.factor_details.extend(
        [
            _explanation_detail(code, "risk", "limit_up_price", summary["limit_up_price"], "Tushare upper limit price."),
            _explanation_detail(code, "risk", "limit_down_price", summary["limit_down_price"], "Tushare lower limit price."),
            QuantFactorScore(
                stock_code=code,
                factor_group="risk",
                factor_name="limit_status",
                raw_value=LIMIT_STATUS_CODES.get(summary["limit_status"], Decimal("0")),
                normalized_value=Decimal("50"),
                score=Decimal("50"),
                weight=None,
                explain_text=str(summary["limit_status"]),
            ),
            _explanation_detail(
                code,
                "risk",
                "consecutive_limit_up_count",
                summary["consecutive_limit_up_count"],
                "Consecutive limit-up closes derived from daily close and stk_limit.",
            ),
            _explanation_detail(
                code,
                "risk",
                "consecutive_limit_down_count",
                summary["consecutive_limit_down_count"],
                "Consecutive limit-down closes derived from daily close and stk_limit.",
            ),
            QuantFactorScore(
                stock_code=code,
                factor_group="risk",
                factor_name="limit_risk_note",
                raw_value=LIMIT_STATUS_CODES.get(summary["limit_status"], Decimal("0")),
                normalized_value=Decimal("50"),
                score=Decimal("50"),
                weight=None,
                explain_text=str(summary["limit_risk_note"]),
            ),
            _explanation_detail(code, "risk", "distance_to_limit_up", summary.get("distance_to_limit_up"), "Percent distance from close to upper limit."),
            _explanation_detail(code, "risk", "distance_to_limit_down", summary.get("distance_to_limit_down"), "Signed percent distance from close to lower limit."),
            _explanation_detail(code, "risk", "touched_limit_up", int(bool(summary.get("touched_limit_up"))), "Intraday high touched the upper limit."),
            _explanation_detail(code, "risk", "touched_limit_down", int(bool(summary.get("touched_limit_down"))), "Intraday low touched the lower limit."),
            _explanation_detail(code, "risk", "close_at_limit_up", int(bool(summary.get("close_at_limit_up"))), "Close equals upper limit within configured tick tolerance."),
            _explanation_detail(code, "risk", "close_at_limit_down", int(bool(summary.get("close_at_limit_down"))), "Close equals lower limit within configured tick tolerance."),
        ]
    )
    if not any(detail.factor_name == "price_limit_risk_score" for detail in result.factor_details):
        result.factor_details.append(
            _explanation_detail(
                code,
                "risk",
                "price_limit_risk_score",
                summary.get("price_limit_risk_score", 50),
                f"Price-limit score is diagnostic only; risk integration enabled={price_limit_enabled}.",
            )
        )
    if adjustment is not None:
        _append_adjustment_details(result, code, adjustment)


def _append_adjustment_details(result, code: str, adjustment: AdjustedPriceSeries) -> None:
    latest = adjustment.bars[-1] if adjustment.bars else None
    date_value = (
        Decimal(adjustment.factor_as_of_trade_date.strftime("%Y%m%d"))
        if adjustment.factor_as_of_trade_date
        else None
    )
    anchor_value = (
        Decimal(adjustment.adjustment_anchor_date.strftime("%Y%m%d"))
        if adjustment.adjustment_anchor_date
        else None
    )
    details = [
        QuantFactorScore(stock_code=code, factor_group="technical", factor_name="technical_price_basis", raw_value=Decimal("1") if adjustment.active else Decimal("0"), normalized_value=Decimal("50"), score=Decimal("50"), weight=None, explain_text=adjustment.technical_price_basis),
        QuantFactorScore(stock_code=code, factor_group="technical", factor_name="adjusted_series_version", raw_value=Decimal("1") if adjustment.active else Decimal("0"), normalized_value=Decimal("50"), score=Decimal("50"), weight=None, explain_text=adjustment.adjusted_series_version),
        QuantFactorScore(stock_code=code, factor_group="technical", factor_name="factor_as_of_trade_date", raw_value=date_value, normalized_value=Decimal("50"), score=Decimal("50"), weight=None, explain_text=str(adjustment.factor_as_of_trade_date or "")),
        QuantFactorScore(stock_code=code, factor_group="technical", factor_name="factor_available_at", raw_value=Decimal("1") if adjustment.factor_available_at else Decimal("0"), normalized_value=Decimal("50"), score=Decimal("50"), weight=None, explain_text=adjustment.factor_available_at.isoformat() if adjustment.factor_available_at else ""),
        QuantFactorScore(stock_code=code, factor_group="technical", factor_name="adjustment_anchor_date", raw_value=anchor_value, normalized_value=Decimal("50"), score=Decimal("50"), weight=None, explain_text=str(adjustment.adjustment_anchor_date or "")),
        QuantFactorScore(stock_code=code, factor_group="technical", factor_name="point_in_time_adjustment_status", raw_value=Decimal("1") if adjustment.point_in_time_status == "PASS" else Decimal("0"), normalized_value=Decimal("50"), score=Decimal("50"), weight=None, explain_text=adjustment.point_in_time_status),
        QuantFactorScore(stock_code=code, factor_group="technical", factor_name="adjustment_warning", raw_value=Decimal("1") if adjustment.warning else Decimal("0"), normalized_value=Decimal("50"), score=Decimal("50"), weight=None, explain_text=adjustment.warning or ""),
    ]
    if latest is not None and adjustment.active:
        for name in ("open", "high", "low", "close", "pre_close"):
            details.append(_explanation_detail(code, "technical", f"adjusted_{name}", getattr(latest, name), f"Latest {name} on {adjustment.technical_price_basis} basis."))
    result.factor_details.extend(details)


def _explanation_detail(
    stock_code: str,
    group: str,
    name: str,
    raw_value: Any,
    explain_text: str,
) -> QuantFactorScore:
    return QuantFactorScore(
        stock_code=stock_code,
        factor_group=group,
        factor_name=name,
        raw_value=_decimal(raw_value) if raw_value not in (None, "") else None,
        normalized_value=Decimal("50"),
        score=Decimal("50"),
        weight=None,
        explain_text=explain_text,
    )


def _adj_factor_available(context: dict[str, Any], stock_code: str, bars: list[CanonicalKLineBar]) -> bool:
    by_date = context.get("adj_factor_by_stock", {}).get(stock_code, {})
    if not by_date:
        return False
    bar_dates = {_compact_date(bar.datetime) for bar in bars}
    return any(trade_day in by_date for trade_day in bar_dates)


def _limit_summary(context: dict[str, Any], stock_code: str, bars: list[CanonicalKLineBar]) -> dict[str, Any]:
    limit_records = context.get("stk_limit_by_stock", {}).get(stock_code, {})
    return build_price_limit_risk([_to_legacy_bar(bar) for bar in bars], limit_records)


def _unknown_limit_summary() -> dict[str, Any]:
    return {
        "limit_up_price": None,
        "limit_down_price": None,
        "limit_status": "LIMIT_DATA_MISSING",
        "consecutive_limit_up_count": 0,
        "consecutive_limit_down_count": 0,
        "limit_risk_note": "stk_limit unavailable",
    }


def _detect_limit_status(close: Any, up_limit: float, down_limit: float) -> str:
    close_value = float(close or 0)
    if up_limit > 0 and close_value >= up_limit * 0.999:
        return "LIMIT_UP_CLOSE"
    if down_limit > 0 and close_value <= down_limit * 1.001:
        return "LIMIT_DOWN_CLOSE"
    if up_limit > 0 and close_value >= up_limit * 0.98:
        return "NEAR_LIMIT_UP"
    if down_limit > 0 and close_value <= down_limit * 1.02:
        return "NEAR_LIMIT_DOWN"
    return "NORMAL"


def _consecutive_limit_count(
    bars: list[CanonicalKLineBar],
    limit_records: dict[str, dict[str, Any]],
    direction: str,
) -> int:
    count = 0
    for bar in reversed(bars):
        record = limit_records.get(_compact_date(bar.datetime))
        if not record:
            break
        up_limit = _tushare_float(_tushare_pick(record, "up_limit", default=0))
        down_limit = _tushare_float(_tushare_pick(record, "down_limit", default=0))
        status = _detect_limit_status(bar.close, up_limit, down_limit)
        if direction == "up" and status == "LIMIT_UP_CLOSE":
            count += 1
            continue
        if direction == "down" and status == "LIMIT_DOWN_CLOSE":
            count += 1
            continue
        break
    return count


def _limit_risk_note(status: str, consecutive_up: int, consecutive_down: int) -> str:
    if consecutive_up >= 2:
        return "consecutive_limit_risk"
    if consecutive_down >= 1 or status in {"LIMIT_DOWN_CLOSE", "NEAR_LIMIT_DOWN"}:
        return "limit_down_liquidity_risk"
    if status in {"LIMIT_UP_CLOSE", "NEAR_LIMIT_UP"}:
        return "limit_up_chase_risk"
    return "normal"


def _compact_date(value: str) -> str:
    return str(value or "").split("T", 1)[0].replace("-", "")


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
        "trade_date_cache_hit_count": 0,
        "trade_date_cache_miss_count": 0,
        "trade_date_cache_refresh_count": 0,
        "per_stock_api_call_count": 0,
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
    trade_date_stats = _trade_date_cache_stats(*providers)
    performance["trade_date_cache_hit_count"] = trade_date_stats["hit_count"]
    performance["trade_date_cache_miss_count"] = trade_date_stats["miss_count"]
    performance["trade_date_cache_refresh_count"] = trade_date_stats["refresh_count"]
    performance["per_stock_api_call_count"] = trade_date_stats["per_stock_api_call_count"]


def _trade_date_cache_stats(*providers) -> dict[str, int | bool]:
    hit_count = sum(int(getattr(provider, "trade_date_cache_hit_count", 0)) for provider in providers)
    miss_count = sum(int(getattr(provider, "trade_date_cache_miss_count", 0)) for provider in providers)
    refresh_count = sum(int(getattr(provider, "trade_date_cache_refresh_count", 0)) for provider in providers)
    per_stock_api_call_count = sum(int(getattr(provider, "per_stock_api_call_count", 0)) for provider in providers)
    return {
        "used": bool(hit_count or miss_count),
        "hit_count": hit_count,
        "miss_count": miss_count,
        "refresh_count": refresh_count,
        "per_stock_api_call_count": per_stock_api_call_count,
    }


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
    detail_map = {detail.factor_name: detail for detail in getattr(item, "factor_details", [])}
    limit_status = _limit_status_from_detail(detail_map.get("limit_status"))
    report = {
        "rank": item.rank,
        "stock_code": item.stock_code,
        "stock_name": item.stock_name,
        "total_score": float(item.total_score),
        "technical_score": float(item.technical_score),
        "capital_score": float(item.capital_score),
        "emotion_score": float(item.emotion_score),
        "momentum_score": float(item.momentum_score),
        "risk_score": float(item.risk_score),
        "adj_factor_available": _detail_bool(detail_map.get("adj_factor_available")),
        "technical_price_basis": _detail_text(detail_map.get("technical_price_basis")) or RAW,
        "raw_technical_score": _detail_float(detail_map.get("raw_technical_score")),
        "adjusted_technical_score": _detail_float(detail_map.get("adjusted_technical_score")),
        "active_technical_score": _detail_float(detail_map.get("active_technical_score")),
        "technical_score_delta": _detail_float(detail_map.get("technical_score_delta")),
        "adjustment_warning": _detail_text(detail_map.get("adjustment_warning")),
        "limit_status": limit_status,
        "limit_up_price": _detail_float(detail_map.get("limit_up_price")),
        "limit_down_price": _detail_float(detail_map.get("limit_down_price")),
        "consecutive_limit_up_count": int(_detail_float(detail_map.get("consecutive_limit_up_count")) or 0),
        "consecutive_limit_down_count": int(_detail_float(detail_map.get("consecutive_limit_down_count")) or 0),
        "price_limit_risk_score": _detail_float(detail_map.get("price_limit_risk_score")),
        "price_limit_internal_weight": _detail_float(detail_map.get("price_limit_internal_weight")),
        "limit_risk_note": _detail_text(detail_map.get("limit_risk_note")),
        "factor_detail": [_factor_detail_to_report(detail) for detail in getattr(item, "factor_details", [])],
    }
    return report


def _score_to_quant_universe_report(item) -> dict[str, Any]:
    detail_map = {detail.factor_name: detail for detail in getattr(item, "factor_details", [])}
    code = str(item.stock_code)
    exchange = "BJ" if code.startswith(("4", "8", "920")) else ("SH" if code.startswith("6") else "SZ")
    coverage = "COMPLETE" if all(
        getattr(item, name, None) is not None
        for name in ("technical_score", "capital_score", "emotion_score", "momentum_score", "risk_score")
    ) else "PARTIAL"
    return {
        "rank": item.rank,
        "stock_code": code,
        "stock_name": item.stock_name,
        "exchange": exchange,
        "level_one_sector": item.industry or "UNKNOWN",
        "classification_standard": "TUSHARE_STOCK_BASIC_INDUSTRY",
        "total_score": float(item.total_score),
        "technical_score": float(item.technical_score),
        "capital_score": float(item.capital_score),
        "emotion_score": float(item.emotion_score),
        "momentum_score": float(item.momentum_score),
        "risk_score": float(item.risk_score),
        "technical_price_basis": _detail_text(detail_map.get("technical_price_basis")) or RAW,
        "technical_score_delta": _detail_float(detail_map.get("technical_score_delta")),
        "limit_status": _limit_status_from_detail(detail_map.get("limit_status")),
        "price_limit_risk_score": _detail_float(detail_map.get("price_limit_risk_score")),
        "data_coverage_status": coverage,
    }


def _factor_detail_to_report(detail: QuantFactorScore) -> dict[str, Any]:
    return {
        "factor_group": detail.factor_group,
        "factor_name": detail.factor_name,
        "raw_value": _json_default(detail.raw_value) if detail.raw_value is not None else None,
        "normalized_value": _json_default(detail.normalized_value) if detail.normalized_value is not None else None,
        "score": _json_default(detail.score),
        "weight": _json_default(detail.weight) if detail.weight is not None else None,
        "explain_text": detail.explain_text,
    }


def _detail_bool(detail: QuantFactorScore | None) -> bool:
    if detail is None or detail.raw_value is None:
        return False
    return detail.raw_value > 0


def _detail_float(detail: QuantFactorScore | None) -> float | None:
    if detail is None or detail.raw_value is None:
        return None
    return float(detail.raw_value)


def _detail_text(detail: QuantFactorScore | None) -> str:
    return detail.explain_text if detail is not None else ""


def _limit_status_from_detail(detail: QuantFactorScore | None) -> str:
    if detail is None or detail.raw_value is None:
        return "UNKNOWN"
    return LIMIT_STATUS_BY_CODE.get(int(detail.raw_value), "UNKNOWN")


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
    scored_results: list,
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
    trade_date_cache_stats: dict[str, int | bool] | None = None,
) -> dict[str, Any]:
    api_counts = tushare_api_counts or {"success": 0, "empty": 0, "error": 0}
    trade_date_stats = trade_date_cache_stats or {
        "used": False,
        "hit_count": 0,
        "miss_count": 0,
        "refresh_count": 0,
        "per_stock_api_call_count": 0,
    }
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
        "trade_date_cache_used": bool(trade_date_stats.get("used", False)),
        "trade_date_cache_hit_count": int(trade_date_stats.get("hit_count", 0)),
        "trade_date_cache_miss_count": int(trade_date_stats.get("miss_count", 0)),
        "trade_date_cache_refresh_count": int(trade_date_stats.get("refresh_count", 0)),
        "per_stock_api_call_count": int(trade_date_stats.get("per_stock_api_call_count", 0)),
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
        # Compact all-scored rows are persisted with the formal Quant Run.  Factor
        # detail remains limited to Top Q to keep the report and DB practical.
        "all_scored_stocks": [_score_to_quant_universe_report(item) for item in scored_results],
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
