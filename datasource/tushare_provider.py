from __future__ import annotations

import json
import hashlib
import math
import os
import time
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from datasource.baostock_provider import BaoStockMarketDataProvider
from datasource.exceptions import DataSourceError
from datasource.interfaces.market_data import MarketDataProvider
from datasource.mock.market_provider import MockMarketDataProvider
from datasource.models.market import (
    CapitalFlowData,
    FinanceData,
    KLineBar,
    LimitPriceData,
    MarketEmotionData,
    MarketStockInfo,
    PreMarketAuctionData,
    RealtimeQuote,
)
from datasource.schemas import ProviderStatus
from backend.core.runtime_paths import tushare_cache_root


CACHE_DIR = tushare_cache_root()
DEFAULT_TOKEN_ENV = "TUSHARE_TOKEN"
MISSING_TOKEN_MESSAGE = "TUSHARE_TOKEN is not configured"
PERMISSION_NEEDLES = (
    "permission",
    "no permission",
    "2002",
    "没有访问该接口的权限",
    "没有权限",
    "积分不足",
    "没有权限",
    "权限",
    "积分",
)
NETWORK_NEEDLES = (
    "connection",
    "timeout",
    "network",
    "proxy",
    "ssl",
    "远程",
)
RATE_LIMIT_NEEDLES = ("rate limit", "too many", "频率", "每分钟最多")
INVALID_PARAMETER_NEEDLES = ("invalid parameter", "参数错误", "参数不正确", "必选参数")


MOJIBAKE_MARKERS = (
    "涓",
    "鍏",
    "鍥",
    "娣",
    "骞",
    "閾",
    "鑲",
    "濂",
    "绉",
    "鏈",
    "璇",
    "熸",
    "锟",
)


class TushareEndpointResult(BaseModel):
    api_name: str
    status: str
    records: list[dict[str, Any]] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    source: str = "tushare"
    source_status: str = "ok"
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.status == "available"


class TushareMarketDataProvider(MarketDataProvider):
    """Manual Tushare Pro provider.

    Tushare is imported only when an endpoint method is called. The real token is
    read from the local environment and is never stored in reports or cache keys.
    """

    def __init__(
        self,
        enabled: bool = True,
        token_env: str = DEFAULT_TOKEN_ENV,
        cache_enabled: bool = True,
        cache_dir: Path | str = CACHE_DIR,
        request_interval_seconds: float = 0.25,
        max_retry: int = 3,
        timeout_seconds: int = 30,
        backup_provider: Any | None = None,
    ) -> None:
        self.name = "tushare"
        self.provider_type = "market_primary_debug"
        self.enabled = enabled
        self.is_mock = False
        self.token_env = token_env
        self.cache_enabled = cache_enabled
        self.cache_dir = Path(cache_dir)
        self.request_interval_seconds = request_interval_seconds
        self.max_retry = max(1, int(max_retry or 1))
        self.timeout_seconds = timeout_seconds
        self.backup_provider = backup_provider
        self.last_source_status = "not_requested"
        self.last_error_type: str | None = None
        self.last_error_message: str | None = None
        self.last_fallback_used = False
        self.last_fallback_reason: str | None = None
        self.cache_hit_count = 0
        self.cache_miss_count = 0
        self.cache_refresh_count = 0
        self.cache_insufficient_count = 0
        self.trade_date_cache_hit_count = 0
        self.trade_date_cache_miss_count = 0
        self.trade_date_cache_refresh_count = 0
        self.per_stock_api_call_count = 0
        self.api_status_counts: dict[str, int] = {}
        self._trade_date_memory_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._pro_client: Any | None = None
        self._fallback = MockMarketDataProvider()

    def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            provider_type=self.provider_type,
            enabled=self.enabled,
            is_mock=self.is_mock,
            healthy=self.enabled,
            status="manual_debug" if self.enabled else "disabled",
            message="Tushare manual debug provider uses local environment configuration.",
        )

    def get_provider_info(self) -> ProviderStatus:
        return self.health_check()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "source": "tushare",
            "source_status": self.last_source_status,
            "error_type": self.last_error_type,
            "error_message": self.last_error_message,
            "fallback_used": self.last_fallback_used,
            "fallback_reason": self.last_fallback_reason,
            "cache_enabled": self.cache_enabled,
            "request_interval_seconds": self.request_interval_seconds,
            "timeout_seconds": self.timeout_seconds,
            "api_status_counts": dict(self.api_status_counts),
            "trade_date_cache_hit_count": self.trade_date_cache_hit_count,
            "trade_date_cache_miss_count": self.trade_date_cache_miss_count,
            "trade_date_cache_refresh_count": self.trade_date_cache_refresh_count,
            "per_stock_api_call_count": self.per_stock_api_call_count,
        }

    def token_configured(self) -> bool:
        return bool(self._token())

    def query_endpoint(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | list[str] | None = None,
        required_fields: set[str] | list[str] | None = None,
        limit: int | None = None,
        use_cache: bool = True,
        write_cache: bool = True,
    ) -> TushareEndpointResult:
        params = _clean_params(params or {})
        fields_text = ",".join(fields) if isinstance(fields, list) else fields
        required = set(required_fields or [])
        cache_path = self._cache_path(api_name, params, fields_text)

        if use_cache and self.cache_enabled and cache_path.exists():
            try:
                records = json.loads(cache_path.read_text(encoding="utf-8"))
                if limit is not None:
                    records = records[:limit]
                self._mark_success("cache")
                self.cache_hit_count += 1
                result = _endpoint_result(api_name, records, required, source_status="cache")
                self._record_api_status(result.status)
                return result
            except (OSError, json.JSONDecodeError, TypeError):
                pass

        if not self._token():
            self._mark_error("MissingToken", MISSING_TOKEN_MESSAGE)
            self._record_api_status("not_configured")
            return TushareEndpointResult(
                api_name=api_name,
                status="not_configured",
                error_type="MissingToken",
                error_message=MISSING_TOKEN_MESSAGE,
                source_status="not_configured",
            )

        last_exc: Exception | None = None
        for attempt in range(self.max_retry):
            try:
                if _is_per_stock_api_call(api_name, params):
                    self.per_stock_api_call_count += 1
                frame = self._call_api(api_name, params, fields_text)
                records = _records(frame)
                if limit is not None:
                    records = records[:limit]
                result = _endpoint_result(api_name, records, required)
                self._mark_success(result.status)
                if self.cache_enabled:
                    self.cache_miss_count += 1
                if self.cache_enabled and write_cache and result.status in {"available", "empty"}:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    if cache_path.exists():
                        self.cache_refresh_count += 1
                    cache_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
                self._record_api_status(result.status)
                return result
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < self.max_retry:
                    self._respect_request_interval()

        assert last_exc is not None
        status = _status_from_exception(last_exc)
        error_type = _error_type(last_exc)
        error_message = self._redact(str(last_exc))
        self._mark_error(error_type, error_message)
        self._record_api_status(status)
        return TushareEndpointResult(
            api_name=api_name,
            status=status,
            error_type=error_type,
            error_message=error_message,
                source_status=status,
            )

    def trade_date_cache_path(self, api_name: str, trade_date: str | None) -> Path:
        normalized_date = _ts_date(trade_date) or "latest"
        if api_name in {"ths_index", "ths_member"}:
            return self.cache_dir / "concept" / api_name / f"{normalized_date}.json"
        return self.cache_dir / "trade_date" / api_name / f"{normalized_date}.json"

    def query_trade_date_endpoint(
        self,
        api_name: str,
        trade_date: str | None,
        fields: str | list[str] | None = None,
        required_fields: set[str] | list[str] | None = None,
        use_cache: bool = True,
        refresh_cache: bool = False,
    ) -> TushareEndpointResult:
        normalized_date = _ts_date(trade_date)
        fields_text = ",".join(fields) if isinstance(fields, list) else fields
        fields_text = fields_text or _trade_date_fields(api_name)
        required = set(required_fields or _trade_date_required_fields(api_name))
        cache_path = self.trade_date_cache_path(api_name, normalized_date)
        memory_key = (api_name, normalized_date or "latest")

        if use_cache and self.cache_enabled and not refresh_cache:
            cached_records = self._read_trade_date_cache(cache_path, memory_key)
            if cached_records is not None:
                self._mark_success("trade_date_cache")
                self.trade_date_cache_hit_count += 1
                result = _endpoint_result(api_name, cached_records, required, source_status="trade_date_cache")
                self._record_api_status(result.status)
                return result

        if not self._token():
            self._mark_error("MissingToken", MISSING_TOKEN_MESSAGE)
            self._record_api_status("not_configured")
            return TushareEndpointResult(
                api_name=api_name,
                status="not_configured",
                error_type="MissingToken",
                error_message=MISSING_TOKEN_MESSAGE,
                source_status="not_configured",
            )

        params = _trade_date_params(api_name, normalized_date)
        last_exc: Exception | None = None
        for attempt in range(self.max_retry):
            try:
                frame = self._call_api(api_name, params, fields_text)
                records = _records(frame)
                result = _endpoint_result(api_name, records, required)
                self._mark_success(result.status)
                if self.cache_enabled:
                    self.trade_date_cache_miss_count += 1
                    if cache_path.exists():
                        self.trade_date_cache_refresh_count += 1
                    if result.status in {"available", "empty", "field_mismatch"}:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
                        self._trade_date_memory_cache[memory_key] = records
                self._record_api_status(result.status)
                return result
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < self.max_retry:
                    self._respect_request_interval()

        assert last_exc is not None
        status = _status_from_exception(last_exc)
        error_type = _error_type(last_exc)
        error_message = self._redact(str(last_exc))
        self._mark_error(error_type, error_message)
        self._record_api_status(status)
        return TushareEndpointResult(
            api_name=api_name,
            status=status,
            error_type=error_type,
            error_message=error_message,
            source_status=status,
        )

    def get_open_trade_dates(self, start_date: str | None, end_date: str | None) -> list[str]:
        result = self.get_trade_calendar(start_date=start_date, end_date=end_date, is_open="1")
        if result.status == "available":
            dates = [
                str(_pick(row, "cal_date", default=""))
                for row in result.records
                if str(_pick(row, "is_open", default="1")) in {"1", "1.0", "True", "true"} and _pick(row, "cal_date")
            ]
            if dates:
                return sorted(set(dates))

        cached_dates = self._cached_trade_dates(start_date, end_date)
        if cached_dates:
            return cached_dates
        if result.status not in {"available", "empty"}:
            raise DataSourceError(f"Tushare trade_cal request failed: {result.error_message or result.status}")
        return []

    def _cached_trade_dates(self, start_date: str | None, end_date: str | None) -> list[str]:
        start = _ts_date(start_date)
        end = _ts_date(end_date)
        cache_dir = self.cache_dir / "trade_date" / "daily"
        if not self.cache_enabled or not cache_dir.is_dir():
            return []
        return sorted(
            path.stem
            for path in cache_dir.glob("*.json")
            if path.stem.isdigit()
            and len(path.stem) == 8
            and (not start or path.stem >= start)
            and (not end or path.stem <= end)
        )

    def get_trade_date_records(
        self,
        api_name: str,
        start_date: str | None,
        end_date: str | None,
        fields: str | list[str] | None = None,
        required_fields: set[str] | list[str] | None = None,
        use_cache: bool = True,
        refresh_cache: bool = False,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for trade_day in self.get_open_trade_dates(start_date, end_date):
            result = self.query_trade_date_endpoint(
                api_name,
                trade_day,
                fields=fields,
                required_fields=required_fields,
                use_cache=use_cache,
                refresh_cache=refresh_cache,
            )
            if result.status == "available":
                records.extend(result.records)
            elif result.status == "field_mismatch":
                self.cache_insufficient_count += 1
            elif result.status not in {"empty", "permission_denied"}:
                self.cache_insufficient_count += 1
        return records

    def get_stock_list(self) -> list[MarketStockInfo]:
        result = self._records_or_raise(
            "stock_basic",
            params={"exchange": "", "list_status": "L"},
            fields="ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,is_hs",
            required_fields={"ts_code", "symbol", "name"},
        )
        stocks: list[MarketStockInfo] = []
        for row in result:
            ts_code = str(_pick(row, "ts_code", default=""))
            code = _plain_code(ts_code or _pick(row, "symbol", default=""))
            if not code:
                continue
            stocks.append(
                MarketStockInfo(
                    code=code,
                    name=str(_pick(row, "name", default=code)),
                    market=_market_from_ts_code(ts_code, _pick(row, "exchange", "market", default="")),
                    industry=str(_pick(row, "industry", default="")),
                    status=_stock_status(_pick(row, "list_status", default="L")),
                    source="tushare",
                    raw_data=row,
                )
            )
        return stocks

    def get_trade_calendar(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        is_open: str | None = None,
    ) -> TushareEndpointResult:
        return self.query_endpoint(
            "trade_cal",
            params={"exchange": "", "start_date": _ts_date(start_date), "end_date": _ts_date(end_date), "is_open": is_open},
            fields="exchange,cal_date,is_open,pretrade_date",
            required_fields={"cal_date", "is_open"},
        )

    def get_hs_connect_constituents(self, hs_type: str = "SH") -> TushareEndpointResult:
        return self.query_endpoint("hs_const", params={"hs_type": hs_type})

    def get_st_stock_list(self) -> TushareEndpointResult:
        result = self.query_endpoint(
            "stock_basic",
            params={"exchange": "", "list_status": "L"},
            fields="ts_code,symbol,name,market,exchange,list_status",
            required_fields={"ts_code", "name"},
        )
        if result.available:
            result.records = [
                row
                for row in result.records
                if "ST" in str(_pick(row, "name", default="")).upper()
            ]
            result.status = "available" if result.records else "empty"
        return result

    def get_realtime(self, stock_code: str) -> RealtimeQuote:
        rows = self._records_or_raise(
            "daily",
            params={"ts_code": _ts_code(stock_code)},
            fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
            required_fields={"ts_code", "trade_date", "close"},
        )
        if not rows:
            raise DataSourceError(f"Tushare realtime proxy returned no daily row for {_plain_code(stock_code)}")
        row = rows[0]
        close = _float(_pick(row, "close", default=0))
        return RealtimeQuote(
            stock_code=_plain_code(_pick(row, "ts_code", default=stock_code)),
            price=close,
            open=_float(_pick(row, "open", default=close)),
            high=_float(_pick(row, "high", default=close)),
            low=_float(_pick(row, "low", default=close)),
            pre_close=_float(_pick(row, "pre_close", default=close)),
            volume=int(_float(_pick(row, "vol", default=0))),
            amount=_float(_pick(row, "amount", default=0)),
            change_percent=_float(_pick(row, "pct_chg", "change_percent", default=0)),
            datetime=_iso_date(_pick(row, "trade_date", default=date.today().isoformat())),
            source="tushare",
            raw_data=row,
        )

    def get_kline(
        self,
        stock_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "daily",
    ) -> list[KLineBar]:
        self.last_fallback_used = False
        self.last_fallback_reason = None
        api_name = _frequency_api(frequency)
        if api_name == "daily" and self.cache_enabled:
            try:
                cached_bars = self._get_kline_from_trade_date_cache(stock_code, start_date, end_date, frequency)
                if cached_bars:
                    return cached_bars
            except Exception:
                self.cache_insufficient_count += 1
        try:
            rows = self._records_or_raise(
                api_name,
                params={
                    "ts_code": _ts_code(stock_code),
                    "start_date": _ts_date(start_date),
                    "end_date": _ts_date(end_date),
                },
                fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
                required_fields={"ts_code", "trade_date", "open", "high", "low", "close"},
            )
            bars = [_kline_bar(row, frequency) for row in rows]
            if bars:
                return bars
            raise DataSourceError(f"Tushare {api_name} returned empty kline for {_plain_code(stock_code)}")
        except Exception as exc:
            fallback = self.backup_provider
            if fallback is not None:
                self.last_fallback_used = True
                self.last_fallback_reason = f"tushare_{api_name}_failed:{exc.__class__.__name__}"
                return fallback.get_kline(stock_code, start_date, end_date, frequency=frequency)
            raise

    def get_adj_factor(
        self,
        stock_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> TushareEndpointResult:
        return self.query_endpoint(
            "adj_factor",
            params={"ts_code": _ts_code(stock_code), "start_date": _ts_date(start_date), "end_date": _ts_date(end_date)},
            fields="ts_code,trade_date,adj_factor",
            required_fields={"ts_code", "trade_date", "adj_factor"},
        )

    def get_daily_basic(self, stock_code: str, trade_date: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint(
            "daily_basic",
            params={"ts_code": _ts_code(stock_code), "trade_date": _ts_date(trade_date)},
            fields="ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv,limit_status",
            required_fields={"ts_code", "trade_date"},
        )

    def get_limit_price(self, stock_code: str, trade_date: str | None = None) -> LimitPriceData:
        rows = self._records_or_raise(
            "stk_limit",
            params={"ts_code": _ts_code(stock_code), "trade_date": _ts_date(trade_date)},
            fields="trade_date,ts_code,pre_close,up_limit,down_limit",
            required_fields={"ts_code", "trade_date", "up_limit", "down_limit"},
        )
        if not rows:
            return self._fallback.get_limit_price(_plain_code(stock_code)).model_copy(
                update={"source": "tushare_fallback", "source_status": "fallback"}
            )
        row = rows[0]
        return LimitPriceData(
            stock_code=_plain_code(_pick(row, "ts_code", default=stock_code)),
            limit_up_price=_float(_pick(row, "up_limit", default=0)),
            limit_down_price=_float(_pick(row, "down_limit", default=0)),
            trade_date=_iso_date(_pick(row, "trade_date", default=trade_date or date.today().isoformat())),
            source="tushare",
            raw_data=row,
        )

    def get_etf_list(self) -> TushareEndpointResult:
        return self.query_endpoint("fund_basic", params={"market": "E"}, required_fields={"ts_code", "name"})

    def get_etf_daily(self, ts_code: str, start_date: str | None = None, end_date: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint(
            "fund_daily",
            params={"ts_code": ts_code, "start_date": _ts_date(start_date), "end_date": _ts_date(end_date)},
            required_fields={"ts_code", "trade_date"},
        )

    def get_option_list(self) -> TushareEndpointResult:
        return self.query_endpoint("opt_basic", params={"exchange": "SSE"})

    def get_finance(self, stock_code: str) -> FinanceData:
        basic = self.get_daily_basic(stock_code).records
        indicator = self.get_fina_indicator(stock_code).records
        basic_row = basic[0] if basic else {}
        indicator_row = indicator[0] if indicator else {}
        return FinanceData(
            stock_code=_plain_code(stock_code),
            revenue=_float(_pick(indicator_row, "revenue", "or_yoy", default=0)),
            profit=_float(_pick(indicator_row, "profit_dedt", "netprofit_margin", default=0)),
            pe=_float(_pick(basic_row, "pe", default=0)),
            pb=_float(_pick(basic_row, "pb", default=0)),
            roe=_float(_pick(indicator_row, "roe", "roe_dt", default=0)),
            debt_ratio=_float(_pick(indicator_row, "debt_to_assets", default=0)),
            source="tushare",
            raw_data={"daily_basic": basic_row, "fina_indicator": indicator_row},
        )

    def get_income(self, stock_code: str) -> TushareEndpointResult:
        return self.query_endpoint("income", params={"ts_code": _ts_code(stock_code)}, limit=1)

    def get_stock_company(self, exchange: str = "SSE", *, use_cache: bool = True) -> TushareEndpointResult:
        return self.query_endpoint("stock_company", params={"exchange": exchange}, use_cache=use_cache)

    def get_stock_basic_result(self, *, use_cache: bool = True) -> TushareEndpointResult:
        return self.query_endpoint(
            "stock_basic",
            params={"exchange": "", "list_status": "L"},
            required_fields={"ts_code", "name"},
            use_cache=use_cache,
        )

    def get_fundamental_period_batch(
        self,
        interface: str,
        period: str,
        *,
        mainbz_type: str | None = None,
        use_cache: bool = False,
    ) -> TushareEndpointResult:
        allowed = {
            "income": "income_vip",
            "balancesheet": "balancesheet_vip",
            "cashflow": "cashflow_vip",
            "fina_indicator": "fina_indicator_vip",
            "mainbz": "fina_mainbz_vip",
            "forecast": "forecast",
            "express": "express",
            "fina_audit": "fina_audit",
            "disclosure_date": "disclosure_date",
            "dividend": "dividend",
        }
        try:
            api_name = allowed[interface]
        except KeyError as exc:
            raise ValueError(f"Unsupported fundamental interface: {interface}") from exc
        params = {"period": _ts_date(period)}
        if interface == "mainbz":
            if mainbz_type not in {"P", "I", "D"}:
                raise ValueError("mainbz_type must be P, I or D")
            params["type"] = mainbz_type
        elif interface in {"disclosure_date", "dividend"}:
            params = {"end_date": _ts_date(period)}
        return self.query_endpoint(api_name, params=params, use_cache=use_cache)

    def get_balancesheet(self, stock_code: str) -> TushareEndpointResult:
        return self.query_endpoint("balancesheet", params={"ts_code": _ts_code(stock_code)}, limit=1)

    def get_cashflow(self, stock_code: str) -> TushareEndpointResult:
        return self.query_endpoint("cashflow", params={"ts_code": _ts_code(stock_code)}, limit=1)

    def get_fina_indicator(self, stock_code: str) -> TushareEndpointResult:
        return self.query_endpoint("fina_indicator", params={"ts_code": _ts_code(stock_code)}, limit=1)

    def get_macro_snapshot(self) -> dict[str, TushareEndpointResult]:
        return {
            "shibor": self.query_endpoint("shibor", limit=1),
            "shibor_lpr": self.query_endpoint("shibor_lpr", limit=1),
        }

    def get_pledge(self, stock_code: str) -> TushareEndpointResult:
        return self.query_endpoint("pledge_stat", params={"ts_code": _ts_code(stock_code)}, limit=1)

    def get_share_unlock(self, stock_code: str) -> TushareEndpointResult:
        return self.query_endpoint("share_float", params={"ts_code": _ts_code(stock_code)}, limit=1)

    def get_repurchase(self, stock_code: str) -> TushareEndpointResult:
        return self.query_endpoint("repurchase", params={"ts_code": _ts_code(stock_code)}, limit=1)

    def get_holder_trade(self, stock_code: str) -> TushareEndpointResult:
        return self.query_endpoint("stk_holdertrade", params={"ts_code": _ts_code(stock_code)}, limit=1)

    def get_top_list(self, trade_date: str | None = None, stock_code: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint(
            "top_list",
            params={"trade_date": _ts_date(trade_date), "ts_code": _ts_code(stock_code) if stock_code else None},
            required_fields={"trade_date", "ts_code"},
        )

    def get_top_inst(self, trade_date: str | None = None, stock_code: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint(
            "top_inst",
            params={"trade_date": _ts_date(trade_date), "ts_code": _ts_code(stock_code) if stock_code else None},
        )

    def get_margin_summary(self, trade_date: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint("margin", params={"trade_date": _ts_date(trade_date)})

    def get_margin_detail(self, stock_code: str | None = None, trade_date: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint(
            "margin_detail",
            params={"ts_code": _ts_code(stock_code) if stock_code else None, "trade_date": _ts_date(trade_date)},
        )

    def get_concept_list(self) -> TushareEndpointResult:
        return self.query_endpoint(
            "ths_index",
            params={"exchange": "A", "type": "N"},
            required_fields={"ts_code", "name"},
        )

    def get_concept_members(self, concept_id: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint(
            "ths_member",
            params={"ts_code": concept_id},
            required_fields={"ts_code", "con_code"},
        )

    def get_moneyflow(self, stock_code: str, start_date: str | None = None, end_date: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint(
            "moneyflow",
            params={"ts_code": _ts_code(stock_code), "start_date": _ts_date(start_date), "end_date": _ts_date(end_date)},
            required_fields={"ts_code", "trade_date"},
        )

    def get_market_moneyflow(self, trade_date: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint("moneyflow", params={"trade_date": _ts_date(trade_date)})

    def get_capital_flow(self, stock_code: str) -> CapitalFlowData:
        moneyflow = self.get_moneyflow(stock_code).records
        daily_basic = self.get_daily_basic(stock_code).records
        flow_row = moneyflow[0] if moneyflow else {}
        basic_row = daily_basic[0] if daily_basic else {}
        return CapitalFlowData(
            stock_code=_plain_code(stock_code),
            main_net_inflow=_float(_pick(flow_row, "net_mf_amount", default=0)) * 10000,
            large_order_net_inflow=(
                _float(_pick(flow_row, "buy_lg_amount", default=0))
                + _float(_pick(flow_row, "buy_elg_amount", default=0))
                - _float(_pick(flow_row, "sell_lg_amount", default=0))
                - _float(_pick(flow_row, "sell_elg_amount", default=0))
            )
            * 10000,
            amount=_float(_pick(basic_row, "amount", default=0)),
            turnover_rate=_float(_pick(basic_row, "turnover_rate", default=0)),
            source="tushare",
            raw_data={"moneyflow": flow_row, "daily_basic": basic_row},
        )

    def get_broker_recommendations(self, month: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint("broker_recommend", params={"month": month or date.today().strftime("%Y%m")})

    def get_chip_distribution(self, stock_code: str, trade_date: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint(
            "cyq_chips",
            params={"ts_code": _ts_code(stock_code), "trade_date": _ts_date(trade_date)},
        )

    def get_tushare_factors(self, stock_code: str, trade_date: str | None = None) -> TushareEndpointResult:
        return self.query_endpoint(
            "stk_factor_pro",
            params={"ts_code": _ts_code(stock_code), "trade_date": _ts_date(trade_date)},
            limit=1,
        )

    def get_market_emotion(self) -> MarketEmotionData:
        concept = self.get_concept_list()
        return MarketEmotionData(
            limit_up_count=0,
            limit_down_count=0,
            consecutive_limit_up_height=0,
            break_board_rate=0.0,
            hot_industries=[
                str(_pick(row, "name", "concept_name", default=""))
                for row in concept.records[:10]
                if _pick(row, "name", "concept_name", default="")
            ],
            source="tushare",
            source_status=concept.status,
            message=concept.error_message or "",
            raw_data={"concept_status": concept.status, "sample": concept.records[:10]},
        )

    def get_pre_market_auction(self, stock_code: str) -> PreMarketAuctionData:
        raise NotImplementedError("Tushare pre-market auction is not enabled in this phase.")

    def _records_or_raise(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | list[str] | None = None,
        required_fields: set[str] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        result = self.query_endpoint(api_name, params=params, fields=fields, required_fields=required_fields)
        if result.status in {"available", "empty"}:
            return result.records
        raise DataSourceError(f"Tushare {api_name} request failed: {result.error_message or result.status}")

    def _call_api(self, api_name: str, params: dict[str, Any], fields: str | None):
        pro = self._pro()
        self._respect_request_interval()
        if hasattr(pro, api_name):
            method = getattr(pro, api_name)
            return _invoke_tushare_method(method, params, fields)
        if hasattr(pro, "query"):
            query_params = dict(params)
            if fields:
                query_params["fields"] = fields
            return pro.query(api_name, **query_params)
        raise DataSourceError(f"Tushare SDK object does not expose {api_name}")

    def _pro(self):
        if self._pro_client is None:
            tushare = import_module("tushare")
            self._pro_client = tushare.pro_api(self._token())
        return self._pro_client

    def _token(self) -> str:
        return os.getenv(self.token_env, "").strip()

    def _cache_path(self, api_name: str, params: dict[str, Any], fields: str | None) -> Path:
        key = json.dumps({"api_name": api_name, "params": params, "fields": fields}, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{api_name}_{digest}.json"

    def _read_trade_date_cache(
        self,
        cache_path: Path,
        memory_key: tuple[str, str],
    ) -> list[dict[str, Any]] | None:
        if memory_key in self._trade_date_memory_cache:
            return self._trade_date_memory_cache[memory_key]
        if not cache_path.exists():
            return None
        try:
            records = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(records, list):
            return None
        self._trade_date_memory_cache[memory_key] = records
        return records

    def _get_kline_from_trade_date_cache(
        self,
        stock_code: str,
        start_date: str | None,
        end_date: str | None,
        frequency: str,
    ) -> list[KLineBar]:
        rows = self.get_trade_date_records(
            "daily",
            start_date,
            end_date,
            fields=_trade_date_fields("daily"),
            required_fields=_trade_date_required_fields("daily"),
            use_cache=True,
            refresh_cache=False,
        )
        plain_code = _plain_code(stock_code)
        bars = [_kline_bar(row, frequency) for row in rows if _plain_code(_pick(row, "ts_code", default="")) == plain_code]
        bars.sort(key=lambda item: item.datetime)
        return bars

    def _mark_success(self, status: str) -> None:
        self.last_source_status = status
        self.last_error_type = None
        self.last_error_message = None

    def _mark_error(self, error_type: str, error_message: str) -> None:
        self.last_source_status = "error"
        self.last_error_type = error_type
        self.last_error_message = self._redact(error_message)

    def _redact(self, value: str) -> str:
        token = self._token()
        if token:
            return value.replace(token, "[redacted]")
        return value

    def _respect_request_interval(self) -> None:
        if self.request_interval_seconds > 0:
            time.sleep(self.request_interval_seconds)

    def reset_cache_stats(self) -> None:
        self.cache_hit_count = 0
        self.cache_miss_count = 0
        self.cache_refresh_count = 0
        self.cache_insufficient_count = 0
        self.trade_date_cache_hit_count = 0
        self.trade_date_cache_miss_count = 0
        self.trade_date_cache_refresh_count = 0
        self.per_stock_api_call_count = 0
        self.api_status_counts = {}

    def _record_api_status(self, status: str) -> None:
        self.api_status_counts[status] = self.api_status_counts.get(status, 0) + 1


TushareProvider = TushareMarketDataProvider


def _invoke_tushare_method(method, params: dict[str, Any], fields: str | None):
    try:
        return method(**params, fields=fields) if fields else method(**params)
    except TypeError:
        return method(**params)


def _endpoint_result(
    api_name: str,
    records: list[dict[str, Any]],
    required_fields: set[str],
    source_status: str = "ok",
) -> TushareEndpointResult:
    missing_fields: list[str] = []
    if records and required_fields:
        present = set(records[0])
        missing_fields = sorted(required_fields - present)
    if missing_fields:
        status = "field_mismatch"
    elif records:
        status = "available"
    else:
        status = "empty"
    return TushareEndpointResult(
        api_name=api_name,
        status=status,
        records=records,
        missing_fields=missing_fields,
        source_status=source_status if status != "field_mismatch" else "field_mismatch",
        raw_data={"row_count": len(records)},
    )


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        return _normalize_records([dict(row) for row in frame.to_dict(orient="records")])
    if isinstance(frame, list):
        return _normalize_records([dict(item) for item in frame])
    if isinstance(frame, dict):
        data = frame.get("data")
        if isinstance(data, dict) and "fields" in data and "items" in data:
            fields = [str(field) for field in data.get("fields", [])]
            return _normalize_records([dict(zip(fields, item)) for item in data.get("items", [])])
        if all(isinstance(value, list) for value in frame.values()):
            keys = list(frame)
            return _normalize_records([dict(zip(keys, item)) for item in zip(*frame.values())])
        return _normalize_records([dict(frame)])
    return []


def _normalize_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _normalize_value(value) for key, value in row.items()} for row in records]


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _repair_mojibake(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _repair_mojibake(value: str) -> str:
    if not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value
    try:
        repaired = value.encode("gb18030").decode("utf-8")
    except UnicodeError:
        try:
            repaired = value.encode("gb18030", errors="ignore").decode("utf-8", errors="ignore")
        except UnicodeError:
            return value
    return repaired if _mojibake_score(repaired) < _mojibake_score(value) else value


def _mojibake_score(value: str) -> int:
    return sum(value.count(marker) for marker in MOJIBAKE_MARKERS) + value.count("\ufffd") * 2


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value not in (None, "")}


def _env_file_value(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""
    prefix = f"{key}="
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith(prefix):
            continue
        return stripped[len(prefix):].strip().strip('"').strip("'")
    return ""


def _trade_date_fields(api_name: str) -> str | None:
    return {
        "daily": "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        "daily_basic": "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv,limit_status",
        "adj_factor": "ts_code,trade_date,adj_factor",
        "stk_limit": "trade_date,ts_code,pre_close,up_limit,down_limit",
        "moneyflow": "ts_code,trade_date,buy_sm_amount,sell_sm_amount,buy_md_amount,sell_md_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount",
        "top_list": None,
        "margin": None,
        "margin_detail": None,
        "ths_index": None,
        "ths_member": None,
    }.get(api_name)


def _trade_date_required_fields(api_name: str) -> set[str]:
    return {
        "daily": {"ts_code", "trade_date", "open", "high", "low", "close"},
        "daily_basic": {"ts_code", "trade_date"},
        "adj_factor": {"ts_code", "trade_date", "adj_factor"},
        "stk_limit": {"ts_code", "trade_date"},
        "moneyflow": {"ts_code", "trade_date"},
        "top_list": {"ts_code", "trade_date"},
        "margin": {"trade_date"},
        "margin_detail": {"ts_code", "trade_date"},
        "ths_index": {"ts_code", "name"},
        "ths_member": {"ts_code", "con_code"},
    }.get(api_name, set())


def _trade_date_params(api_name: str, trade_date: str | None) -> dict[str, Any]:
    if api_name == "ths_index":
        return {"exchange": "A", "type": "N"}
    if api_name == "ths_member":
        return {}
    return {"trade_date": trade_date}


def _is_per_stock_api_call(api_name: str, params: dict[str, Any]) -> bool:
    return api_name in {
        "daily", "daily_basic", "adj_factor", "stk_limit", "moneyflow",
        "income", "balancesheet", "cashflow", "fina_indicator",
        "income_vip", "balancesheet_vip", "cashflow_vip", "fina_indicator_vip",
        "fina_mainbz_vip", "forecast", "express", "fina_audit", "disclosure_date", "dividend",
    } and bool(params.get("ts_code"))


def _pick(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _plain_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    digits = "".join(char for char in text if char.isdigit())
    return digits.zfill(6) if digits else ""


def _ts_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        code, suffix = text.split(".", 1)
        return f"{code.zfill(6)}.{suffix}"
    code = _plain_code(text)
    if not code:
        return ""
    if code.startswith(("8", "4", "920")):
        return f"{code}.BJ"
    if code.startswith(("6", "688")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _market_from_ts_code(ts_code: str, fallback: Any = "") -> str:
    text = str(ts_code or "").upper()
    if text.endswith(".SH"):
        return "SH"
    if text.endswith(".SZ"):
        return "SZ"
    if text.endswith(".BJ"):
        return "BJ"
    return str(fallback or "")


def _stock_status(value: Any) -> str:
    normalized = str(value or "").upper()
    if normalized in {"L", "NORMAL", "LISTED", "1"}:
        return "NORMAL"
    if normalized in {"D", "DELISTED"}:
        return "DELISTED"
    if normalized in {"P", "SUSPENDED"}:
        return "SUSPENDED"
    return normalized or "UNKNOWN"


def _frequency_api(value: str) -> str:
    normalized = str(value or "daily").lower()
    if normalized in {"1d", "d", "day"}:
        return "daily"
    if normalized in {"1w", "w", "week"}:
        return "weekly"
    if normalized in {"1m", "m", "month"}:
        return "monthly"
    return normalized


def _kline_bar(row: dict[str, Any], frequency: str) -> KLineBar:
    close = _float(_pick(row, "close", default=0))
    pre_close = _float(_pick(row, "pre_close", default=close))
    return KLineBar(
        stock_code=_plain_code(_pick(row, "ts_code", default="")),
        datetime=_iso_date(_pick(row, "trade_date", default="")),
        open=_float(_pick(row, "open", default=0)),
        high=_float(_pick(row, "high", default=0)),
        low=_float(_pick(row, "low", default=0)),
        close=close,
        pre_close=pre_close,
        volume=int(_float(_pick(row, "vol", "volume", default=0))),
        amount=_float(_pick(row, "amount", default=0)),
        turnover_rate=_float(_pick(row, "turnover_rate", default=0)),
        change_percent=_float(_pick(row, "pct_chg", "change_percent", default=0)),
        source="tushare",
        raw_data={"row": row, "frequency": frequency},
    )


def _ts_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).split("T", 1)[0]
    return text.replace("-", "")


def _iso_date(value: Any) -> str:
    text = str(value or "").split("T", 1)[0]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text or date.today().isoformat()


def _status_from_exception(exc: Exception) -> str:
    text = f"{exc.__class__.__name__}: {exc}".lower()
    if any(needle in text for needle in PERMISSION_NEEDLES):
        return "permission_denied"
    if any(needle in text for needle in RATE_LIMIT_NEEDLES):
        return "rate_limited"
    if any(needle in text for needle in INVALID_PARAMETER_NEEDLES):
        return "invalid_parameter"
    if any(needle in text for needle in NETWORK_NEEDLES):
        return "error"
    return "error"


def _error_type(exc: Exception) -> str:
    text = f"{exc.__class__.__name__}: {exc}".lower()
    if any(needle in text for needle in PERMISSION_NEEDLES):
        return "NOT_AUTHORIZED"
    if any(needle in text for needle in RATE_LIMIT_NEEDLES):
        return "RATE_LIMITED"
    if any(needle in text for needle in INVALID_PARAMETER_NEEDLES):
        return "INVALID_PARAMETER"
    if any(needle in text for needle in NETWORK_NEEDLES):
        return "TIMEOUT" if "timeout" in text else "PROVIDER_ERROR"
    return "PROVIDER_ERROR"


def _safe_cache_key(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)[:220]
