from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from datasource.models.market import (
    CapitalFlowData as CanonicalCapitalFlowData,
    FinanceData as CanonicalFinanceData,
    KLineBar as CanonicalKLineBar,
    LimitPriceData as CanonicalLimitPriceData,
    MarketEmotionData as CanonicalMarketEmotionData,
    MarketStockInfo as CanonicalMarketStockInfo,
    PreMarketAuctionData as CanonicalPreMarketAuctionData,
    RealtimeQuote as CanonicalRealtimeQuote,
)
from datasource.models.news import NewsItem as CanonicalNewsItem
from datasource.models.overseas import (
    CommodityData as CanonicalCommodityData,
    FxRateData as CanonicalFxRateData,
    OverseasIndexData as CanonicalOverseasIndexData,
    OverseasStockData as CanonicalOverseasStockData,
)


class DataSourceModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProviderStatus(DataSourceModel):
    name: str
    provider_type: str
    enabled: bool
    is_mock: bool
    healthy: bool
    status: str
    message: str | None = None


class StockInfo(DataSourceModel):
    stock_code: str
    name: str
    market: str
    industry: str | None = None
    list_date: date | None = None
    status: str = "NORMAL"


class RealtimeQuote(DataSourceModel):
    stock_code: str
    name: str | None = None
    current_price: Decimal
    change_percent: Decimal
    volume: int
    amount: Decimal
    quote_time: datetime


class KlineBar(DataSourceModel):
    stock_code: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal
    volume: int
    amount: Decimal
    frequency: str = "1d"


class FinanceSnapshot(DataSourceModel):
    stock_code: str
    date: date
    revenue: Decimal
    profit: Decimal
    pe: Decimal
    pb: Decimal
    roe: Decimal
    debt_ratio: Decimal


class CapitalFlowSnapshot(DataSourceModel):
    stock_code: str
    trade_date: date
    main_net_inflow: Decimal
    retail_net_inflow: Decimal
    turnover_rate: Decimal
    volume_ratio: Decimal


class MarketEmotionSnapshot(DataSourceModel):
    trade_date: date
    limit_up_count: int
    limit_down_count: int
    up_count: int
    down_count: int
    emotion_score: Decimal = Field(ge=0, le=100)


class LimitPriceInfo(DataSourceModel):
    stock_code: str
    trade_date: date
    previous_close: Decimal
    limit_up_price: Decimal
    limit_down_price: Decimal


class PreMarketAuctionInfo(DataSourceModel):
    stock_code: str
    trade_date: date
    auction_price: Decimal
    auction_volume: int
    auction_amount: Decimal
    auction_change_percent: Decimal
    auction_strength_score: Decimal


class NewsItem(DataSourceModel):
    title: str
    content: str
    source: str
    publish_time: datetime
    importance: Decimal
    sentiment: str
    related_stocks: list[str] = Field(default_factory=list)


class OverseasMarketSnapshot(DataSourceModel):
    name: str
    market: str
    value: Decimal
    change_percent: Decimal
    snapshot_time: datetime
    category: str
