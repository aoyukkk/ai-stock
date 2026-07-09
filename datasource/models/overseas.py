from __future__ import annotations

from pydantic import BaseModel


class OverseasIndexData(BaseModel):
    name: str
    symbol: str
    price: float
    change_percent: float
    datetime: str


class CommodityData(BaseModel):
    name: str
    symbol: str
    price: float
    change_percent: float
    datetime: str


class FxRateData(BaseModel):
    pair: str
    rate: float
    change_percent: float
    datetime: str


class OverseasStockData(BaseModel):
    name: str
    symbol: str
    price: float
    change_percent: float
    datetime: str
