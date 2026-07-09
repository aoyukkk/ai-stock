from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DataSourceModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MarketStockInfo(DataSourceModel):
    code: str
    name: str
    market: str
    industry: str = ""
    status: str = "NORMAL"
    latest_price: float | None = None
    amount: float | None = None
    volume: int | None = None
    source: str = ""
    raw_data: dict | None = None


class RealtimeQuote(DataSourceModel):
    stock_code: str
    price: float
    open: float
    high: float
    low: float
    pre_close: float
    volume: int
    amount: float
    change_percent: float
    datetime: str
    source: str = ""
    raw_data: dict | None = None


class KLineBar(DataSourceModel):
    stock_code: str
    datetime: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    volume: int
    amount: float
    turnover_rate: float
    change_percent: float
    source: str = ""
    raw_data: dict | None = None


class FinanceData(DataSourceModel):
    stock_code: str
    revenue: float
    profit: float
    pe: float
    pb: float
    roe: float
    debt_ratio: float
    source: str = ""
    raw_data: dict | None = None


class CapitalFlowData(DataSourceModel):
    stock_code: str
    main_net_inflow: float
    large_order_net_inflow: float
    amount: float
    turnover_rate: float
    source: str = ""
    raw_data: dict | None = None


class MarketEmotionData(DataSourceModel):
    limit_up_count: int
    limit_down_count: int
    consecutive_limit_up_height: int
    break_board_rate: float
    hot_industries: list[str] = Field(default_factory=list)
    source: str = ""
    source_status: str = "ok"
    message: str = ""
    raw_data: dict | None = None


class LimitPriceData(DataSourceModel):
    stock_code: str
    limit_up_price: float
    limit_down_price: float
    trade_date: str
    source: str = ""
    source_status: str = "ok"
    message: str = ""
    raw_data: dict | None = None


class PreMarketAuctionData(DataSourceModel):
    stock_code: str
    trade_date: str
    auction_price: float
    auction_volume: int
    auction_amount: float
    auction_change_percent: float
    auction_strength_score: float
    source: str = ""
    raw_data: dict | None = None
