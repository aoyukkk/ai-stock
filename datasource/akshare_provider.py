from __future__ import annotations

import json
import os
from datetime import date
from contextlib import contextmanager
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


CACHE_DIR = Path("data/cache/akshare")
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)

CN_CODE = "\u4ee3\u7801"
CN_NAME = "\u540d\u79f0"
CN_SHORT_NAME = "\u80a1\u7968\u7b80\u79f0"
CN_INDUSTRY = "\u884c\u4e1a"
CN_LATEST_PRICE = "\u6700\u65b0\u4ef7"
CN_AMOUNT = "\u6210\u4ea4\u989d"
CN_VOLUME = "\u6210\u4ea4\u91cf"
CN_PRE_CLOSE = "\u6628\u6536"
CN_OPEN = "\u4eca\u5f00"
CN_HIGH = "\u6700\u9ad8"
CN_LOW = "\u6700\u4f4e"
CN_CHANGE_PERCENT = "\u6da8\u8dcc\u5e45"
CN_DATE = "\u65e5\u671f"
CN_CLOSE = "\u6536\u76d8"
CN_K_OPEN = "\u5f00\u76d8"
CN_TURNOVER = "\u6362\u624b\u7387"


class AKShareMarketDataProvider(MarketDataProvider):
    """Manual debug provider for AKShare.

    AKShare is imported only when a method is invoked. The debug limit-price
    rule below uses a simplified 10% A-share limit calculation; STAR Market,
    ChiNext, BSE, and ST-specific limits need a later exchange-rule extension.
    """

    def __init__(
        self,
        enabled: bool = True,
        cache_enabled: bool = True,
        cache_dir: Path | str = CACHE_DIR,
        proxy_mode: str = "env",
        request_timeout_seconds: int = 10,
    ) -> None:
        self.name = "akshare"
        self.provider_type = "market_debug"
        self.enabled = enabled
        self.is_mock = False
        self.cache_enabled = cache_enabled
        self.cache_dir = Path(cache_dir)
        self.proxy_mode = _validate_proxy_mode(proxy_mode)
        self.request_timeout_seconds = request_timeout_seconds
        self.last_source_status = "not_requested"
        self.last_error_type: str | None = None
        self.last_error_message: str | None = None
        self.last_proxy_env_detected = _proxy_env_detected()
        self._fallback = MockMarketDataProvider()

    def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            provider_type=self.provider_type,
            enabled=self.enabled,
            is_mock=self.is_mock,
            healthy=self.enabled,
            status="manual_debug",
            message="AKShare debug provider is available only by manual command/API.",
        )

    def get_provider_info(self) -> ProviderStatus:
        return self.health_check()

    def get_stock_list(self) -> list[MarketStockInfo]:
        try:
            records = self._cached_records("stock_zh_a_spot_em", lambda ak: ak.stock_zh_a_spot_em())
        except Exception as exc:
            raise self._data_source_error("stock list", exc) from exc

        stocks: list[MarketStockInfo] = []
        for row in records:
            code = _code(_pick(row, CN_CODE, "code", "symbol"))
            if not code:
                continue
            stocks.append(
                MarketStockInfo(
                    code=code,
                    name=str(_pick(row, CN_NAME, CN_SHORT_NAME, "name", default=code)),
                    market=_market_from_code(code),
                    industry=str(_pick(row, CN_INDUSTRY, "industry", default="")),
                    status="NORMAL",
                    latest_price=_optional_float(_pick(row, CN_LATEST_PRICE, "price", "current")),
                    amount=_optional_float(_pick(row, CN_AMOUNT, "amount")),
                    volume=_optional_int(_pick(row, CN_VOLUME, "volume")),
                    source="akshare",
                    source_status=self.last_source_status,
                    error_type=self.last_error_type,
                    error_message=self.last_error_message,
                    raw_data=row,
                )
            )
        return stocks

    def get_realtime(self, stock_code: str) -> RealtimeQuote:
        target = _code(stock_code)
        try:
            records = self._cached_records("stock_zh_a_spot_em", lambda ak: ak.stock_zh_a_spot_em())
        except Exception as exc:
            raise self._data_source_error("realtime", exc) from exc

        for row in records:
            code = _code(_pick(row, CN_CODE, "code", "symbol"))
            if code != target:
                continue
            price = _float(_pick(row, CN_LATEST_PRICE, "price", "current"))
            pre_close = _float(_pick(row, CN_PRE_CLOSE, "pre_close", default=price))
            return RealtimeQuote(
                stock_code=target,
                price=price,
                open=_float(_pick(row, CN_OPEN, "open", default=price)),
                high=_float(_pick(row, CN_HIGH, "high", default=price)),
                low=_float(_pick(row, CN_LOW, "low", default=price)),
                pre_close=pre_close,
                volume=int(_float(_pick(row, CN_VOLUME, "volume", default=0))),
                amount=_float(_pick(row, CN_AMOUNT, "amount", default=0)),
                change_percent=_float(_pick(row, CN_CHANGE_PERCENT, "change_percent", default=0)),
                datetime=date.today().isoformat(),
                source="akshare",
                source_status=self.last_source_status,
                error_type=self.last_error_type,
                error_message=self.last_error_message,
                raw_data=row,
            )
        raise DataSourceError(f"AKShare realtime quote not found for {target}")

    def get_kline(
        self,
        stock_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "daily",
    ) -> list[KLineBar]:
        code = _code(stock_code)
        cache_key = f"hist_{code}_{frequency}_{_ak_date(start_date)}_{_ak_date(end_date)}"
        try:
            records = self._cached_records(
                cache_key,
                lambda ak: ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily" if frequency == "daily" else frequency,
                    start_date=_ak_date(start_date),
                    end_date=_ak_date(end_date),
                    adjust="",
                ),
            )
        except Exception as exc:
            raise self._data_source_error("kline", exc) from exc

        bars: list[KLineBar] = []
        for row in records:
            close = _float(_pick(row, CN_CLOSE, "close", default=0))
            pre_close = _float(_pick(row, CN_PRE_CLOSE, "pre_close", default=close))
            bars.append(
                KLineBar(
                    stock_code=code,
                    datetime=str(_pick(row, CN_DATE, "date", "datetime", default="")),
                    open=_float(_pick(row, CN_K_OPEN, "open", default=0)),
                    high=_float(_pick(row, CN_HIGH, "high", default=0)),
                    low=_float(_pick(row, CN_LOW, "low", default=0)),
                    close=close,
                    pre_close=pre_close,
                    volume=int(_float(_pick(row, CN_VOLUME, "volume", default=0))),
                    amount=_float(_pick(row, CN_AMOUNT, "amount", default=0)),
                    turnover_rate=_float(_pick(row, CN_TURNOVER, "turnover_rate", default=0)),
                    change_percent=_float(_pick(row, CN_CHANGE_PERCENT, "change_percent", default=0)),
                    source="akshare",
                    source_status=self.last_source_status,
                    error_type=self.last_error_type,
                    error_message=self.last_error_message,
                    raw_data=row,
                )
            )
        return bars

    def get_finance(self, stock_code: str) -> FinanceData:
        return self._fallback.get_finance(stock_code).model_copy(update={"source": "akshare_fallback"})

    def get_capital_flow(self, stock_code: str) -> CapitalFlowData:
        return self._fallback.get_capital_flow(stock_code).model_copy(update={"source": "akshare_fallback"})

    def get_market_emotion(self) -> MarketEmotionData:
        fallback = self._fallback.get_market_emotion()
        return fallback.model_copy(
            update={
                "source": "akshare",
                "source_status": "fallback",
                "message": "AKShare market emotion API unavailable or unstable",
            }
        )

    def get_limit_price(self, stock_code: str) -> LimitPriceData:
        try:
            quote = self.get_realtime(stock_code)
            return LimitPriceData(
                stock_code=_code(stock_code),
                limit_up_price=round(quote.pre_close * 1.1, 2),
                limit_down_price=round(quote.pre_close * 0.9, 2),
                trade_date=date.today().isoformat(),
                source="akshare",
                source_status="debug_simplified",
                message="Simplified 10% A-share debug rule; board/ST rules need future enhancement.",
                raw_data=quote.raw_data,
            )
        except Exception:
            return self._fallback.get_limit_price(stock_code).model_copy(
                update={"source": "akshare_fallback", "source_status": "fallback"}
            )

    def get_pre_market_auction(self, stock_code: str) -> PreMarketAuctionData:
        raise NotImplementedError("AKShare pre-market auction debug endpoint is not stable yet.")

    def _cached_records(self, cache_key: str, loader) -> list[dict[str, Any]]:
        cache_path = self.cache_dir / f"{_safe_cache_key(cache_key)}.json"
        if self.cache_enabled and cache_path.exists():
            try:
                self._mark_success("cache")
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

        context = _temporary_no_proxy() if self.proxy_mode == "no_proxy" else _null_context()
        with context:
            frame = loader(self._akshare())
        records = _records(frame)
        self._mark_success("ok")
        if self.cache_enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return records

    @staticmethod
    def _akshare() -> Any:
        return import_module("akshare")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "source": "akshare",
            "source_status": self.last_source_status,
            "error_type": self.last_error_type,
            "error_message": self.last_error_message,
            "proxy_mode": self.proxy_mode,
            "proxy_env_detected": self.last_proxy_env_detected,
            "request_timeout_seconds": self.request_timeout_seconds,
        }

    def _mark_success(self, status: str) -> None:
        self.last_source_status = status
        self.last_error_type = None
        self.last_error_message = None
        self.last_proxy_env_detected = _proxy_env_detected()

    def _data_source_error(self, scope: str, exc: Exception) -> DataSourceError:
        self.last_source_status = "error"
        self.last_error_type = _error_type(exc)
        self.last_error_message = str(exc)
        self.last_proxy_env_detected = _proxy_env_detected()
        suggestion = ""
        if _looks_like_proxy_error(exc):
            if self.proxy_mode == "no_proxy":
                suggestion = " Proxy-style failure persisted in no_proxy mode; check system proxy/VPN settings."
            elif self.last_proxy_env_detected:
                suggestion = " Detected proxy-related failure; retry with --no-proxy or fix proxy environment variables."
            else:
                suggestion = " Proxy-style failure detected; check local network/proxy settings."
        return DataSourceError(
            f"AKShare {scope} request failed "
            f"[{self.last_error_type}]: {self.last_error_message}.{suggestion}"
        )


AKShareProvider = AKShareMarketDataProvider


def _records(frame: Any) -> list[dict[str, Any]]:
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict(orient="records"))
    if isinstance(frame, list):
        return [dict(item) for item in frame]
    return []


def _pick(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    return None if value in (None, "") else _float(value)


def _optional_int(value: Any) -> int | None:
    return None if value in (None, "") else int(_float(value))


def _code(value: Any) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.split(".")[-1]
    digits = "".join(char for char in text if char.isdigit())
    return digits.zfill(6) if digits else ""


def _ak_date(value: str | None) -> str:
    return (value or date.today().isoformat()).replace("-", "")


def _market_from_code(code: str) -> str:
    if code.startswith(("6", "688")):
        return "SH"
    if code.startswith(("8", "4")):
        return "BJ"
    return "SZ"


def _safe_cache_key(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)


def _validate_proxy_mode(value: str) -> str:
    if value not in {"env", "no_proxy"}:
        raise ValueError("AKShare proxy_mode must be 'env' or 'no_proxy'")
    return value


def _proxy_env_detected() -> bool:
    return any(os.environ.get(key) for key in PROXY_ENV_KEYS)


@contextmanager
def _temporary_no_proxy():
    original = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
    try:
        for key in PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _null_context():
    yield


def _error_type(exc: Exception) -> str:
    text = str(exc)
    if _looks_like_proxy_error(exc):
        return "ProxyError"
    if "timeout" in text.lower():
        return "TimeoutError"
    return exc.__class__.__name__


def _looks_like_proxy_error(exc: Exception) -> bool:
    text = f"{exc.__class__.__name__}: {exc}".lower()
    needles = ("proxy", "remote end closed connection", "closed connection", "remotedisconnected")
    return any(needle in text for needle in needles)
