from __future__ import annotations

from datetime import date, timedelta
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


class BaoStockMarketDataProvider(MarketDataProvider):
    """Development debug provider for BaoStock historical data."""

    def __init__(self) -> None:
        self._fallback = MockMarketDataProvider()

    @staticmethod
    def to_baostock_code(stock_code: str) -> str:
        if stock_code.startswith(("sh.", "sz.")):
            return stock_code
        prefix = "sh" if stock_code.startswith(("6", "688")) else "sz"
        return f"{prefix}.{stock_code}"

    def get_stock_list(self) -> list[MarketStockInfo]:
        bs = self._baostock()
        try:
            self._login(bs)
            result = bs.query_all_stock()
            stocks: list[MarketStockInfo] = []
            while result.next():
                row = result.get_row_data()
                code = str(row[0]) if row else ""
                plain_code = code.split(".")[-1]
                stocks.append(
                    MarketStockInfo(
                        code=plain_code,
                        name=plain_code,
                        market=code.split(".")[0].upper() if "." in code else "",
                        industry="",
                        status="NORMAL",
                    )
                )
            return stocks
        except Exception as exc:
            raise DataSourceError(f"BaoStock stock list request failed: {exc}") from exc
        finally:
            self._logout(bs)

    def get_realtime(self, stock_code: str) -> RealtimeQuote:
        raise NotImplementedError("BaoStock is mainly used for historical data debugging.")

    def get_kline(
        self,
        stock_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "daily",
    ) -> list[KLineBar]:
        bs = self._baostock()
        try:
            self._login(bs)
            fields = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"
            result = bs.query_history_k_data_plus(
                self.to_baostock_code(stock_code),
                fields,
                start_date=start_date or (date.today() - timedelta(days=30)).isoformat(),
                end_date=end_date or date.today().isoformat(),
                frequency=_frequency(frequency),
                adjustflag="3",
            )
            bars: list[KLineBar] = []
            while result.next():
                row = result.get_row_data()
                bars.append(
                    KLineBar(
                        stock_code=stock_code,
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
                    )
                )
            return bars
        except Exception as exc:
            raise DataSourceError(f"BaoStock kline request failed: {exc}") from exc
        finally:
            self._logout(bs)

    def get_finance(self, stock_code: str) -> FinanceData:
        return self._fallback.get_finance(stock_code)

    def get_capital_flow(self, stock_code: str) -> CapitalFlowData:
        return self._fallback.get_capital_flow(stock_code)

    def get_market_emotion(self) -> MarketEmotionData:
        return self._fallback.get_market_emotion()

    def get_limit_price(self, stock_code: str) -> LimitPriceData:
        return self._fallback.get_limit_price(stock_code)

    def get_pre_market_auction(self, stock_code: str) -> PreMarketAuctionData:
        raise NotImplementedError("BaoStock does not provide pre-market auction debug data.")

    @staticmethod
    def _baostock() -> Any:
        return import_module("baostock")

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


BaoStockProvider = BaoStockMarketDataProvider


def _frequency(value: str) -> str:
    return {
        "daily": "d",
        "5min": "5",
        "15min": "15",
        "30min": "30",
        "60min": "60",
    }.get(value, value)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
