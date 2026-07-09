from __future__ import annotations

from datetime import date
from typing import Any

from datasource.registry import ProviderRegistry, get_provider_registry
from datasource.schemas import (
    CapitalFlowSnapshot,
    KlineBar,
    LimitPriceInfo,
    NewsItem,
    OverseasMarketSnapshot,
    PreMarketAuctionInfo,
    RealtimeQuote,
    StockInfo,
)


class DataSourceService:
    def __init__(self, registry: ProviderRegistry | None = None) -> None:
        self.registry = registry or get_provider_registry()

    def get_provider_statuses(self) -> list[dict[str, Any]]:
        return [
            provider.health_check().model_dump(mode="json")
            for provider in self.registry.list_providers()
        ]

    def get_stock_universe(self) -> list[StockInfo]:
        return self.registry.get_default_market_provider().get_stock_list()

    def get_realtime_quotes(self, stock_codes: list[str]) -> list[RealtimeQuote]:
        return self.registry.get_default_market_provider().get_realtime_quotes(stock_codes)

    def get_kline(
        self,
        stock_code: str,
        start_date: date,
        end_date: date,
        frequency: str = "1d",
    ) -> list[KlineBar]:
        return self.registry.get_default_market_provider().get_kline(
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
        )

    def get_limit_price(self, stock_code: str, trade_date: date) -> LimitPriceInfo:
        return self.registry.get_default_market_provider().get_limit_price(
            stock_code=stock_code,
            trade_date=trade_date,
        )

    def get_capital_flow(self, stock_code: str) -> CapitalFlowSnapshot:
        return self.registry.get_default_market_provider().get_capital_flow(stock_code)

    def get_pre_market_auction(self, stock_code: str, trade_date: date) -> PreMarketAuctionInfo:
        return self.registry.get_default_market_provider().get_pre_market_auction(
            stock_code=stock_code,
            trade_date=trade_date,
        )

    def get_latest_news(self, limit: int = 50) -> list[NewsItem]:
        return self.registry.get_default_news_provider().get_latest_news(limit=limit)

    def get_overseas_snapshot(self) -> dict[str, list[OverseasMarketSnapshot]]:
        provider = self.registry.get_default_overseas_provider()
        return {
            "indices": provider.get_overseas_indices(),
            "leaders": provider.get_overseas_leaders(),
            "fx": provider.get_fx_rates(),
            "commodities": provider.get_commodities(),
            "crypto": provider.get_crypto_market(),
        }
