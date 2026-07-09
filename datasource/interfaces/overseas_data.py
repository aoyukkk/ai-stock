from __future__ import annotations

from abc import ABC, abstractmethod

from datasource.models.overseas import CommodityData, FxRateData, OverseasIndexData, OverseasStockData


class OverseasDataProvider(ABC):
    @abstractmethod
    def get_global_indices(self) -> list[OverseasIndexData]:
        raise NotImplementedError

    @abstractmethod
    def get_us_market_leaders(self) -> list[OverseasStockData]:
        raise NotImplementedError

    @abstractmethod
    def get_commodities(self) -> list[CommodityData]:
        raise NotImplementedError

    @abstractmethod
    def get_fx_rates(self) -> list[FxRateData]:
        raise NotImplementedError

    @abstractmethod
    def get_overseas_stock(self, symbol: str) -> OverseasStockData:
        raise NotImplementedError
