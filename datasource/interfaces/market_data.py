from __future__ import annotations

from abc import ABC, abstractmethod

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


class MarketDataProvider(ABC):
    @abstractmethod
    def get_stock_list(self) -> list[MarketStockInfo]:
        raise NotImplementedError

    @abstractmethod
    def get_realtime(self, stock_code: str) -> RealtimeQuote:
        raise NotImplementedError

    @abstractmethod
    def get_kline(
        self,
        stock_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "daily",
    ) -> list[KLineBar]:
        raise NotImplementedError

    @abstractmethod
    def get_finance(self, stock_code: str) -> FinanceData:
        raise NotImplementedError

    @abstractmethod
    def get_capital_flow(self, stock_code: str) -> CapitalFlowData:
        raise NotImplementedError

    @abstractmethod
    def get_market_emotion(self) -> MarketEmotionData:
        raise NotImplementedError

    @abstractmethod
    def get_limit_price(self, stock_code: str) -> LimitPriceData:
        raise NotImplementedError

    @abstractmethod
    def get_pre_market_auction(self, stock_code: str) -> PreMarketAuctionData:
        raise NotImplementedError
