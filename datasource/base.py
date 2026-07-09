from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

from datasource.interfaces.market_data import MarketDataProvider as CanonicalMarketDataProvider
from datasource.interfaces.news_data import NewsDataProvider as CanonicalNewsDataProvider
from datasource.interfaces.overseas_data import OverseasDataProvider as CanonicalOverseasDataProvider
from datasource.schemas import (
    CapitalFlowSnapshot,
    FinanceSnapshot,
    KlineBar,
    LimitPriceInfo,
    MarketEmotionSnapshot,
    NewsItem,
    OverseasMarketSnapshot,
    PreMarketAuctionInfo,
    ProviderStatus,
    RealtimeQuote,
    StockInfo,
)


class BaseDataProvider(ABC):
    name: str
    provider_type: str
    enabled: bool
    is_mock: bool

    def __init__(
        self,
        name: str,
        provider_type: str,
        enabled: bool = False,
        is_mock: bool = False,
    ) -> None:
        self.name = name
        self.provider_type = provider_type
        self.enabled = enabled
        self.is_mock = is_mock

    @abstractmethod
    def health_check(self) -> ProviderStatus:
        """Return provider health without exposing secrets."""

    def get_provider_info(self) -> ProviderStatus:
        return self.health_check()


class MarketDataProvider(CanonicalMarketDataProvider, BaseDataProvider):
    def get_realtime(self, stock_code: str):
        quotes = self.get_realtime_quotes([stock_code])
        if not quotes:
            raise NotImplementedError(f"No realtime quote available for {stock_code}")
        return quotes[0]

    @abstractmethod
    def get_stock_list(self) -> list[StockInfo]:
        raise NotImplementedError

    @abstractmethod
    def get_realtime_quotes(self, stock_codes: list[str]) -> list[RealtimeQuote]:
        raise NotImplementedError

    @abstractmethod
    def get_kline(
        self,
        stock_code: str,
        start_date: date,
        end_date: date,
        frequency: str = "1d",
    ) -> list[KlineBar]:
        raise NotImplementedError

    @abstractmethod
    def get_finance(self, stock_code: str) -> FinanceSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_capital_flow(self, stock_code: str) -> CapitalFlowSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_market_emotion(self) -> MarketEmotionSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_limit_price(self, stock_code: str, trade_date: date) -> LimitPriceInfo:
        raise NotImplementedError

    @abstractmethod
    def get_pre_market_auction(
        self,
        stock_code: str,
        trade_date: date,
    ) -> PreMarketAuctionInfo:
        raise NotImplementedError


class NewsDataProvider(CanonicalNewsDataProvider, BaseDataProvider):
    @abstractmethod
    def get_latest_news(self, limit: int = 50) -> list[NewsItem]:
        raise NotImplementedError

    def get_stock_news(self, stock_code: str) -> list[NewsItem]:
        return [
            item
            for item in self.search_news(stock_code)
            if stock_code in getattr(item, "related_stocks", [])
        ]

    def get_announcements(self, stock_code: str) -> list[NewsItem]:
        return self.search_news(stock_code)

    def get_policy_news(self) -> list[NewsItem]:
        return self.search_news("policy")

    @abstractmethod
    def search_news(
        self,
        keyword: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[NewsItem]:
        raise NotImplementedError


class OverseasDataProvider(CanonicalOverseasDataProvider, BaseDataProvider):
    def get_global_indices(self):
        return self.get_overseas_indices()

    def get_us_market_leaders(self):
        return self.get_overseas_leaders()

    def get_overseas_stock(self, symbol: str):
        raise NotImplementedError(f"Overseas stock lookup is unavailable for {symbol}")

    @abstractmethod
    def get_overseas_indices(self) -> list[OverseasMarketSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def get_overseas_leaders(self) -> list[OverseasMarketSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def get_fx_rates(self) -> list[OverseasMarketSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def get_commodities(self) -> list[OverseasMarketSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def get_crypto_market(self) -> list[OverseasMarketSnapshot]:
        raise NotImplementedError
