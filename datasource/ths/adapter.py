from __future__ import annotations

from datasource.interfaces.market_data import MarketDataProvider
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


THS_NOT_APPROVED = "THS/iFinD API integration will be implemented after API access is approved."


class THSMarketDataProvider(MarketDataProvider):
    def get_stock_list(self) -> list[MarketStockInfo]:
        raise NotImplementedError(THS_NOT_APPROVED)

    def get_realtime(self, stock_code: str) -> RealtimeQuote:
        raise NotImplementedError(THS_NOT_APPROVED)

    def get_kline(
        self,
        stock_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "daily",
    ) -> list[KLineBar]:
        raise NotImplementedError(THS_NOT_APPROVED)

    def get_finance(self, stock_code: str) -> FinanceData:
        raise NotImplementedError(THS_NOT_APPROVED)

    def get_capital_flow(self, stock_code: str) -> CapitalFlowData:
        raise NotImplementedError(THS_NOT_APPROVED)

    def get_market_emotion(self) -> MarketEmotionData:
        raise NotImplementedError(THS_NOT_APPROVED)

    def get_limit_price(self, stock_code: str) -> LimitPriceData:
        raise NotImplementedError(THS_NOT_APPROVED)

    def get_pre_market_auction(self, stock_code: str) -> PreMarketAuctionData:
        raise NotImplementedError(THS_NOT_APPROVED)
