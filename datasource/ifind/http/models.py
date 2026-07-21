from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class IFindHttpModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["IFIND_HTTP"] = "IFIND_HTTP"
    data_status: str


class IndexDailyBar(IFindHttpModel):
    index_code: str
    datetime: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    index_name: str | None = None
    pre_close: float | None = None
    change_percent: float | None = None
    provider_time: str | None = None


class IndexRealtimeQuote(IFindHttpModel):
    index_code: str
    datetime: str
    latest: float
    open: float
    high: float
    low: float
    volume: float
    amount: float
    index_name: str | None = None
    pre_close: float | None = None
    change_percent: float | None = None
    provider_time: str | None = None
    observed_delay_seconds: float | None = None
    market_session: str | None = None


class RealtimeQuote(IFindHttpModel):
    stock_code: str
    datetime: str
    latest: float
    open: float
    high: float
    low: float
    volume: float
    amount: float
    stock_name: str | None = None
    pre_close: float | None = None
    change_percent: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None
    provider_time: str | None = None
    observed_delay_seconds: float | None = None
    market_session: str | None = None


class HistoricalSnapshot(IFindHttpModel):
    stock_code: str
    datetime: str
    latest: float
    open: float
    high: float
    low: float
    pre_close: float
    volume: float
    amount: float
    provider_time: str | None = None


class MinuteBar(IFindHttpModel):
    stock_code: str
    datetime: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    interval: str = "1m"
    change_percent: float | None = None
    provider_time: str | None = None
