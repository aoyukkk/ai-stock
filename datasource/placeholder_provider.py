from __future__ import annotations

from datetime import date, datetime

from datasource.base import MarketDataProvider, NewsDataProvider, OverseasDataProvider
from datasource.exceptions import NOT_IMPLEMENTED_MESSAGE, ProviderNotImplementedError
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


class NotImplementedMixin:
    def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            provider_type=self.provider_type,
            enabled=False,
            is_mock=False,
            healthy=False,
            status="not_implemented",
            message=NOT_IMPLEMENTED_MESSAGE,
        )

    def _raise_not_implemented(self):
        raise ProviderNotImplementedError(NOT_IMPLEMENTED_MESSAGE)


class PlaceholderMarketProvider(NotImplementedMixin, MarketDataProvider):
    def __init__(self, name: str) -> None:
        super().__init__(
            name=name,
            provider_type="market",
            enabled=False,
            is_mock=False,
        )

    def get_stock_list(self) -> list[StockInfo]:
        self._raise_not_implemented()

    def get_realtime_quotes(self, stock_codes: list[str]) -> list[RealtimeQuote]:
        self._raise_not_implemented()

    def get_kline(
        self,
        stock_code: str,
        start_date: date,
        end_date: date,
        frequency: str = "1d",
    ) -> list[KlineBar]:
        self._raise_not_implemented()

    def get_finance(self, stock_code: str) -> FinanceSnapshot:
        self._raise_not_implemented()

    def get_capital_flow(self, stock_code: str) -> CapitalFlowSnapshot:
        self._raise_not_implemented()

    def get_market_emotion(self) -> MarketEmotionSnapshot:
        self._raise_not_implemented()

    def get_limit_price(self, stock_code: str, trade_date: date) -> LimitPriceInfo:
        self._raise_not_implemented()

    def get_pre_market_auction(
        self,
        stock_code: str,
        trade_date: date,
    ) -> PreMarketAuctionInfo:
        self._raise_not_implemented()


class PlaceholderNewsProvider(NotImplementedMixin, NewsDataProvider):
    def __init__(self, name: str) -> None:
        super().__init__(
            name=name,
            provider_type="news",
            enabled=False,
            is_mock=False,
        )

    def get_latest_news(self, limit: int = 50) -> list[NewsItem]:
        self._raise_not_implemented()

    def search_news(
        self,
        keyword: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[NewsItem]:
        self._raise_not_implemented()


class PlaceholderOverseasProvider(NotImplementedMixin, OverseasDataProvider):
    def __init__(self, name: str) -> None:
        super().__init__(
            name=name,
            provider_type="overseas",
            enabled=False,
            is_mock=False,
        )

    def get_overseas_indices(self) -> list[OverseasMarketSnapshot]:
        self._raise_not_implemented()

    def get_overseas_leaders(self) -> list[OverseasMarketSnapshot]:
        self._raise_not_implemented()

    def get_fx_rates(self) -> list[OverseasMarketSnapshot]:
        self._raise_not_implemented()

    def get_commodities(self) -> list[OverseasMarketSnapshot]:
        self._raise_not_implemented()

    def get_crypto_market(self) -> list[OverseasMarketSnapshot]:
        self._raise_not_implemented()
