from __future__ import annotations

import json
import time
from datetime import date, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any

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


CACHE_DIR = Path("data/cache/baostock")


class BaoStockMarketDataProvider(MarketDataProvider):
    """Manual debug provider for BaoStock historical data."""

    def __init__(
        self,
        enabled: bool = True,
        cache_enabled: bool = True,
        cache_dir: Path | str = CACHE_DIR,
        request_interval_seconds: float = 0.0,
        use_kline_cache: bool = True,
        refresh_kline_cache: bool = False,
    ) -> None:
        self.name = "baostock"
        self.provider_type = "market_history_debug"
        self.enabled = enabled
        self.is_mock = False
        self.cache_enabled = cache_enabled
        self.cache_dir = Path(cache_dir)
        self.kline_cache_dir = self.cache_dir / "kline"
        self.use_kline_cache = use_kline_cache
        self.refresh_kline_cache = refresh_kline_cache
        self.cache_hit_count = 0
        self.cache_miss_count = 0
        self.cache_insufficient_count = 0
        self.cache_refresh_count = 0
        self.request_interval_seconds = request_interval_seconds
        self.last_actual_trade_date: str | None = None
        self.last_date_attempts: list[dict[str, Any]] = []
        self.last_error_message: str | None = None
        self._session_bs: Any | None = None
        self._fallback = MockMarketDataProvider()

    def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            provider_type=self.provider_type,
            enabled=self.enabled,
            is_mock=self.is_mock,
            healthy=self.enabled,
            status="manual_debug" if self.enabled else "disabled",
            message="BaoStock manual historical backup provider.",
        )

    def get_provider_info(self) -> ProviderStatus:
        return self.health_check()

    @staticmethod
    def to_baostock_code(stock_code: str) -> str:
        normalized = str(stock_code).lower()
        if normalized.startswith(("sh.", "sz.", "bj.")):
            return normalized
        plain = normalized.split(".")[-1].zfill(6)
        if plain.startswith(("6", "688")):
            return f"sh.{plain}"
        if plain.startswith(("8", "4")):
            # First-pass BSE mapping; verify against live BaoStock coverage later.
            return f"bj.{plain}"
        return f"sz.{plain}"

    def get_stock_list(
        self,
        trade_date: str | None = None,
        max_lookback_days: int = 15,
    ) -> list[MarketStockInfo]:
        cache_suffix = _safe_cache_key(trade_date or "latest")
        cache_path = self.cache_dir / f"query_all_stock_{cache_suffix}.json"
        if self.cache_enabled and cache_path.exists() and trade_date is None:
            try:
                stocks = [_stock_from_cached_item(item) for item in json.loads(cache_path.read_text(encoding="utf-8"))]
                if stocks:
                    self.last_actual_trade_date = _actual_trade_date_from_stocks(stocks)
                    self.last_date_attempts = (
                        [
                            {
                                "date": self.last_actual_trade_date,
                                "row_count": len(stocks),
                                "error_code": "cache",
                                "error_msg": "cache hit",
                            }
                        ]
                        if self.last_actual_trade_date
                        else []
                    )
                    self.last_error_message = None
                    return stocks
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass

        bs = self._session_bs or self._baostock()
        owns_session = self._session_bs is None
        try:
            if owns_session:
                self._login(bs)
            universe = self.get_last_available_stock_universe_date(
                end_date=trade_date,
                max_lookback_days=max_lookback_days,
                bs=bs,
                login_required=False,
            )
            rows = universe["rows"]
            self.last_actual_trade_date = universe["actual_trade_date"]
            self.last_date_attempts = universe["attempts"]
            self.last_error_message = universe["error_message"]
            stocks: list[MarketStockInfo] = []
            for row in rows:
                if not row:
                    continue
                stock = _stock_from_row(row, self.last_actual_trade_date)
                if stock is None:
                    continue
                stocks.append(stock)
            if not stocks and self.last_error_message is None:
                self.last_error_message = "BaoStock stock universe contains no supported stock codes."
            if self.cache_enabled:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps([item.model_dump(mode="json") for item in stocks], ensure_ascii=False),
                    encoding="utf-8",
                )
            return stocks
        except Exception as exc:
            raise DataSourceError(f"BaoStock stock list request failed: {exc}") from exc
        finally:
            if owns_session:
                self._logout(bs)

    def get_last_available_stock_universe_date(
        self,
        end_date: str | None = None,
        max_lookback_days: int = 15,
        bs: Any | None = None,
        login_required: bool = True,
    ) -> dict[str, Any]:
        if max_lookback_days < 0:
            raise ValueError("max_lookback_days must be >= 0")
        target_date = _parse_date(end_date) if end_date else date.today()
        module = bs or self._baostock()
        attempts: list[dict[str, Any]] = []
        should_logout = False
        try:
            if login_required:
                self._login(module)
                should_logout = True
            for offset in range(max_lookback_days + 1):
                day = (target_date - timedelta(days=offset)).isoformat()
                result = _query_all_stock(module, day)
                rows = _collect_query_rows(result)
                attempt = {
                    "date": day,
                    "row_count": len(rows),
                    "error_code": str(getattr(result, "error_code", "")),
                    "error_msg": str(getattr(result, "error_msg", "")),
                }
                attempts.append(attempt)
                if rows:
                    self.last_actual_trade_date = day
                    self.last_date_attempts = attempts
                    self.last_error_message = None
                    return {
                        "actual_trade_date": day,
                        "rows": rows,
                        "attempts": attempts,
                        "error_message": None,
                    }
            message = f"BaoStock stock universe empty after {len(attempts)} date attempts."
            self.last_actual_trade_date = None
            self.last_date_attempts = attempts
            self.last_error_message = message
            return {
                "actual_trade_date": None,
                "rows": [],
                "attempts": attempts,
                "error_message": message,
            }
        finally:
            if should_logout:
                self._logout(module)

    def get_realtime(self, stock_code: str) -> RealtimeQuote:
        raise NotImplementedError("BaoStock is mainly used for historical data debugging.")

    def get_kline(
        self,
        stock_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "daily",
        adjustflag: str = "3",
    ) -> list[KLineBar]:
        start = start_date or (date.today() - timedelta(days=30)).isoformat()
        end = end_date or date.today().isoformat()
        cache_path = self._kline_cache_path(stock_code, start, end, frequency, adjustflag)
        if self.cache_enabled and self.use_kline_cache and not self.refresh_kline_cache:
            bars, cache_status = self.read_kline_cache(stock_code, start, end, frequency, adjustflag)
            if bars and cache_status == "hit":
                self.cache_hit_count += 1
                return bars
            if cache_status == "insufficient":
                self.cache_insufficient_count += 1
        if self.cache_enabled and self.use_kline_cache and self.refresh_kline_cache and cache_path.exists():
            self.cache_refresh_count += 1
        if self.cache_enabled and self.use_kline_cache:
            self.cache_miss_count += 1

        bs = self._session_bs or self._baostock()
        owns_session = self._session_bs is None
        try:
            if owns_session:
                self._login(bs)
            fields = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"
            result = bs.query_history_k_data_plus(
                self.to_baostock_code(stock_code),
                fields,
                start_date=start,
                end_date=end,
                frequency=_frequency(frequency),
                adjustflag=adjustflag,
            )
            self._respect_request_interval()
            bars: list[KLineBar] = []
            while result.next():
                row = result.get_row_data()
                if len(row) < 11:
                    continue
                bars.append(
                    KLineBar(
                        stock_code=str(stock_code).split(".")[-1].zfill(6),
                        datetime=row[0],
                        open=_float(row[2]),
                        high=_float(row[3]),
                        low=_float(row[4]),
                        close=_float(row[5]),
                        pre_close=_float(row[6]),
                        volume=int(_float(row[7])),
                        amount=_float(row[8]),
                        turnover_rate=_float(row[9]),
                        change_percent=_float(row[10]),
                        source="baostock",
                        raw_data={"row": row},
                    )
                )
            if self.cache_enabled and self.use_kline_cache:
                self.write_kline_cache(stock_code, start, end, frequency, adjustflag, bars)
            return bars
        except Exception as exc:
            raise DataSourceError(f"BaoStock kline request failed: {exc}") from exc
        finally:
            if owns_session:
                self._logout(bs)

    def get_finance(self, stock_code: str) -> FinanceData:
        return self._fallback.get_finance(stock_code).model_copy(update={"source": "baostock_fallback"})

    def get_capital_flow(self, stock_code: str) -> CapitalFlowData:
        return self._fallback.get_capital_flow(stock_code).model_copy(update={"source": "baostock_fallback"})

    def get_market_emotion(self) -> MarketEmotionData:
        return self._fallback.get_market_emotion().model_copy(update={"source": "baostock_fallback"})

    def get_limit_price(self, stock_code: str) -> LimitPriceData:
        return self._fallback.get_limit_price(stock_code).model_copy(update={"source": "baostock_fallback"})

    def get_pre_market_auction(self, stock_code: str) -> PreMarketAuctionData:
        raise NotImplementedError("BaoStock does not provide pre-market auction debug data.")

    @staticmethod
    def _baostock() -> Any:
        return import_module("baostock")

    def start_session(self) -> None:
        if self._session_bs is not None:
            return
        bs = self._baostock()
        self._login(bs)
        self._session_bs = bs

    def end_session(self) -> None:
        if self._session_bs is None:
            return
        self._logout(self._session_bs)
        self._session_bs = None

    def batch_session(self) -> "BaoStockBatchSession":
        return BaoStockBatchSession(self)

    def reset_cache_stats(self) -> None:
        self.cache_hit_count = 0
        self.cache_miss_count = 0
        self.cache_insufficient_count = 0
        self.cache_refresh_count = 0

    def read_kline_cache(
        self,
        stock_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "daily",
        adjustflag: str = "3",
    ) -> tuple[list[KLineBar], str]:
        """Read cached kline bars without calling BaoStock."""

        start = _parse_date(start_date) if start_date else None
        end = _parse_date(end_date) if end_date else None
        paths = self._matching_kline_cache_paths(stock_code, frequency, adjustflag)
        if not paths:
            return [], "missing"

        bars = _merge_kline_bars(self._read_kline_cache_file(path) for path in paths)
        if start or end:
            bars = [
                bar
                for bar in bars
                if (start is None or _parse_date(bar.datetime) >= start)
                and (end is None or _parse_date(bar.datetime) <= end)
            ]
        if not bars:
            return [], "insufficient"

        exact_path = self._kline_cache_path(
            stock_code,
            start_date or "",
            end_date or "",
            frequency,
            adjustflag,
        )
        if exact_path.exists() or len(bars) >= 20:
            return bars, "hit"
        return bars, "insufficient"

    def read_all_cached_kline(
        self,
        stock_code: str,
        frequency: str = "daily",
        adjustflag: str = "3",
    ) -> list[KLineBar]:
        paths = self._matching_kline_cache_paths(stock_code, frequency, adjustflag)
        return _merge_kline_bars(self._read_kline_cache_file(path) for path in paths)

    def write_kline_cache(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
        bars: list[KLineBar],
    ) -> Path:
        cache_path = self._kline_cache_path(stock_code, start_date, end_date, frequency, adjustflag)
        self.kline_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps([item.model_dump(mode="json") for item in _merge_kline_bars([bars])], ensure_ascii=False),
            encoding="utf-8",
        )
        return cache_path

    def merge_kline_cache(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
        bars: list[KLineBar],
    ) -> Path:
        merged = _merge_kline_bars([self.read_all_cached_kline(stock_code, frequency, adjustflag), bars])
        return self.write_kline_cache(stock_code, start_date, end_date, frequency, adjustflag, merged)

    def _kline_cache_path(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
    ) -> Path:
        key = "_".join(
            [
                _safe_cache_key(str(stock_code)),
                _safe_cache_key(str(start_date)),
                _safe_cache_key(str(end_date)),
                _safe_cache_key(str(frequency)),
                _safe_cache_key(str(adjustflag)),
            ]
        )
        return self.kline_cache_dir / f"{key}.json"

    def _matching_kline_cache_paths(self, stock_code: str, frequency: str, adjustflag: str) -> list[Path]:
        stock_key = _safe_cache_key(str(stock_code))
        suffix = f"_{_safe_cache_key(str(frequency))}_{_safe_cache_key(str(adjustflag))}.json"
        if not self.kline_cache_dir.exists():
            return []
        return sorted(
            path
            for path in self.kline_cache_dir.glob(f"{stock_key}_*.json")
            if path.name.endswith(suffix)
        )

    @staticmethod
    def _read_kline_cache_file(path: Path) -> list[KLineBar]:
        try:
            return [KLineBar(**item) for item in json.loads(path.read_text(encoding="utf-8"))]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return []

    @staticmethod
    def _login(bs: Any) -> None:
        result = bs.login()
        if getattr(result, "error_code", "0") not in ("0", 0):
            raise DataSourceError(getattr(result, "error_msg", "BaoStock login failed"))

    @staticmethod
    def _logout(bs: Any) -> None:
        try:
            bs.logout()
        except Exception:
            pass

    def _respect_request_interval(self) -> None:
        if self.request_interval_seconds > 0:
            time.sleep(self.request_interval_seconds)


BaoStockProvider = BaoStockMarketDataProvider


class BaoStockBatchSession:
    """Context manager for manual BaoStock batch runs."""

    def __init__(self, provider: BaoStockMarketDataProvider) -> None:
        self.provider = provider

    def __enter__(self) -> BaoStockMarketDataProvider:
        self.provider.start_session()
        return self.provider

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.provider.end_session()
        return False


def _frequency(value: str) -> str:
    return {
        "daily": "d",
        "5min": "5",
        "15min": "15",
        "30min": "30",
        "60min": "60",
    }.get(value, value)


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    normalized = value.split("T", 1)[0]
    if "-" in normalized:
        return date.fromisoformat(normalized)
    return date(int(normalized[:4]), int(normalized[4:6]), int(normalized[6:8]))


def _query_all_stock(bs: Any, day: str):
    try:
        return bs.query_all_stock(day=day)
    except TypeError:
        return bs.query_all_stock()


def _collect_query_rows(result: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    while result.next():
        row = result.get_row_data()
        if row:
            rows.append(row)
    return rows


def _is_stock_code(value: str) -> bool:
    code = str(value).lower()
    if code.startswith("sh."):
        plain = code.split(".", 1)[1]
        return plain.startswith("6")
    if code.startswith("sz."):
        plain = code.split(".", 1)[1]
        return plain.startswith(("0", "3")) and not plain.startswith("399")
    if code.startswith("bj."):
        plain = code.split(".", 1)[1]
        return plain.startswith(("4", "8"))
    return False


def _stock_from_cached_item(item: dict[str, Any]) -> MarketStockInfo:
    raw_row = (item.get("raw_data") or {}).get("row")
    actual_trade_date = (item.get("raw_data") or {}).get("actual_trade_date")
    if raw_row:
        stock = _stock_from_row(raw_row, actual_trade_date)
        if stock is not None:
            return stock
    return MarketStockInfo(**item)


def _stock_from_row(row: list[str], actual_trade_date: str | None) -> MarketStockInfo | None:
    if not row:
        return None
    code = str(row[0])
    if not _is_stock_code(code):
        return None
    plain_code = code.split(".")[-1].zfill(6)
    trade_status = str(row[1]) if len(row) > 1 else "1"
    name = str(row[2]) if len(row) > 2 and row[2] else plain_code
    return MarketStockInfo(
        code=plain_code,
        name=name,
        market=code.split(".")[0].upper() if "." in code else "",
        industry="",
        status="NORMAL" if trade_status in {"1", "NORMAL", "LISTED"} else "SUSPENDED",
        source="baostock",
        raw_data={"row": row, "actual_trade_date": actual_trade_date},
    )


def _actual_trade_date_from_stocks(stocks: list[MarketStockInfo]) -> str | None:
    for stock in stocks:
        raw_data = stock.raw_data or {}
        actual_trade_date = raw_data.get("actual_trade_date")
        if actual_trade_date:
            return str(actual_trade_date)
    return None


def _merge_kline_bars(groups: list[KLineBar] | list[list[KLineBar]] | Any) -> list[KLineBar]:
    by_day: dict[date, KLineBar] = {}
    for group in groups:
        for bar in group:
            try:
                by_day[_parse_date(bar.datetime)] = bar
            except (TypeError, ValueError):
                continue
    return [by_day[day] for day in sorted(by_day)]


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_cache_key(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
