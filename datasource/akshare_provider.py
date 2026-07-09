from __future__ import annotations

from datetime import date
from importlib import import_module
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


class AKShareMarketDataProvider(MarketDataProvider):
    """Development debug provider for AKShare.

    The module does not import or call AKShare until a method is invoked.
    Unit tests should mock the ``akshare`` module and must not depend on live
    network access.
    """

    def __init__(self) -> None:
        self.name = "akshare"
        self.provider_type = "market_debug"
        self.enabled = False
        self.is_mock = False
        self._fallback = MockMarketDataProvider()

    def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            provider_type=self.provider_type,
            enabled=self.enabled,
            is_mock=self.is_mock,
            healthy=False,
            status="debug_disabled",
            message="AKShare debug provider is manual-run only and disabled in default registry.",
        )

    def get_provider_info(self) -> ProviderStatus:
        return self.health_check()

    def get_stock_list(self) -> list[MarketStockInfo]:
        try:
            ak = self._akshare()
            records = _records(ak.stock_zh_a_spot_em())
            stocks: list[MarketStockInfo] = []
            for row in records:
                code = str(_pick(row, "代码", "code", "symbol", default="")).zfill(6)
                if not code:
                    continue
                stocks.append(
                    MarketStockInfo(
                        code=code,
                        name=str(_pick(row, "名称", "name", default=code)),
                        market=_market_from_code(code),
                        industry=str(_pick(row, "行业", "industry", default="")),
                        status="NORMAL",
                    )
                )
            return stocks
        except Exception as exc:
            raise DataSourceError(f"AKShare stock list request failed: {exc}") from exc

    def get_realtime(self, stock_code: str) -> RealtimeQuote:
        try:
            for row in _records(self._akshare().stock_zh_a_spot_em()):
                code = str(_pick(row, "代码", "code", "symbol", default="")).zfill(6)
                if code == stock_code:
                    price = _float(_pick(row, "最新价", "price", "current", default=0))
                    pre_close = _float(_pick(row, "昨收", "pre_close", default=price))
                    return RealtimeQuote(
                        stock_code=stock_code,
                        price=price,
                        open=_float(_pick(row, "今开", "open", default=price)),
                        high=_float(_pick(row, "最高", "high", default=price)),
                        low=_float(_pick(row, "最低", "low", default=price)),
                        pre_close=pre_close,
                        volume=int(_float(_pick(row, "成交量", "volume", default=0))),
                        amount=_float(_pick(row, "成交额", "amount", default=0)),
                        change_percent=_float(_pick(row, "涨跌幅", "change_percent", default=0)),
                        datetime=date.today().isoformat(),
                    )
            raise DataSourceError(f"AKShare realtime quote not found for {stock_code}")
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"AKShare realtime request failed: {exc}") from exc

    def get_kline(
        self,
        stock_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "daily",
    ) -> list[KLineBar]:
        try:
            period = "daily" if frequency == "daily" else frequency
            frame = self._akshare().stock_zh_a_hist(
                symbol=stock_code,
                period=period,
                start_date=_ak_date(start_date),
                end_date=_ak_date(end_date),
                adjust="",
            )
            bars: list[KLineBar] = []
            for row in _records(frame):
                close = _float(_pick(row, "收盘", "close", default=0))
                pre_close = _float(_pick(row, "昨收", "pre_close", default=close))
                bars.append(
                    KLineBar(
                        stock_code=stock_code,
                        datetime=str(_pick(row, "日期", "date", "datetime", default="")),
                        open=_float(_pick(row, "开盘", "open", default=0)),
                        high=_float(_pick(row, "最高", "high", default=0)),
                        low=_float(_pick(row, "最低", "low", default=0)),
                        close=close,
                        pre_close=pre_close,
                        volume=int(_float(_pick(row, "成交量", "volume", default=0))),
                        amount=_float(_pick(row, "成交额", "amount", default=0)),
                        turnover_rate=_float(_pick(row, "换手率", "turnover_rate", default=0)),
                        change_percent=_float(_pick(row, "涨跌幅", "change_percent", default=0)),
                    )
                )
            return bars
        except Exception as exc:
            raise DataSourceError(f"AKShare kline request failed: {exc}") from exc

    def get_finance(self, stock_code: str) -> FinanceData:
        fallback = self._fallback.get_finance(stock_code)
        return fallback.model_copy()

    def get_capital_flow(self, stock_code: str) -> CapitalFlowData:
        fallback = self._fallback.get_capital_flow(stock_code)
        return fallback.model_copy()

    def get_market_emotion(self) -> MarketEmotionData:
        return self._fallback.get_market_emotion()

    def get_limit_price(self, stock_code: str) -> LimitPriceData:
        try:
            quote = self.get_realtime(stock_code)
            return LimitPriceData(
                stock_code=stock_code,
                limit_up_price=round(quote.pre_close * 1.1, 2),
                limit_down_price=round(quote.pre_close * 0.9, 2),
                trade_date=date.today().isoformat(),
            )
        except Exception:
            return self._fallback.get_limit_price(stock_code)

    def get_pre_market_auction(self, stock_code: str) -> PreMarketAuctionData:
        raise NotImplementedError("AKShare pre-market auction debug endpoint is not stable yet.")

    @staticmethod
    def _akshare() -> Any:
        return import_module("akshare")


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


def _ak_date(value: str | None) -> str:
    return (value or date.today().isoformat()).replace("-", "")


def _market_from_code(code: str) -> str:
    return "SH" if code.startswith(("6", "688")) else "SZ"
