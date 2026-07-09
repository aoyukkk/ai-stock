from __future__ import annotations

from datasource.interfaces.overseas_data import OverseasDataProvider
from datasource.models.overseas import CommodityData, FxRateData, OverseasIndexData, OverseasStockData


MOCK_TIME = "2026-01-05T10:00:00+08:00"


class MockOverseasDataProvider(OverseasDataProvider):
    def get_global_indices(self) -> list[OverseasIndexData]:
        return [
            OverseasIndexData(name="S&P 500", symbol="SPX", price=5100.25, change_percent=0.42, datetime=MOCK_TIME),
            OverseasIndexData(name="NASDAQ", symbol="IXIC", price=18020.5, change_percent=0.65, datetime=MOCK_TIME),
            OverseasIndexData(name="Hang Seng", symbol="HSI", price=18520.75, change_percent=-0.31, datetime=MOCK_TIME),
        ]

    def get_us_market_leaders(self) -> list[OverseasStockData]:
        return [
            OverseasStockData(name="NVIDIA", symbol="NVDA", price=915.2, change_percent=1.2, datetime=MOCK_TIME),
            OverseasStockData(name="Apple", symbol="AAPL", price=220.4, change_percent=-0.2, datetime=MOCK_TIME),
        ]

    def get_commodities(self) -> list[CommodityData]:
        return [
            CommodityData(name="Brent Oil", symbol="BRENT", price=82.1, change_percent=-0.2, datetime=MOCK_TIME),
            CommodityData(name="Gold", symbol="XAU", price=2360.5, change_percent=0.4, datetime=MOCK_TIME),
        ]

    def get_fx_rates(self) -> list[FxRateData]:
        return [
            FxRateData(pair="USD/CNY", rate=7.18, change_percent=0.05, datetime=MOCK_TIME),
            FxRateData(pair="EUR/USD", rate=1.08, change_percent=-0.03, datetime=MOCK_TIME),
        ]

    def get_overseas_stock(self, symbol: str) -> OverseasStockData:
        return OverseasStockData(
            name=symbol.upper(),
            symbol=symbol.upper(),
            price=100.0,
            change_percent=0.0,
            datetime=MOCK_TIME,
        )
