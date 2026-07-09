"""Data source abstraction layer."""

from datasource.mock_provider import MockDataProvider
from datasource.mock.market_provider import MockMarketDataProvider
from datasource.mock.news_provider import MockNewsDataProvider
from datasource.mock.overseas_provider import MockOverseasDataProvider
from datasource.registry import ProviderRegistry, get_provider_registry
from datasource.service import DataSourceService

__all__ = [
    "DataSourceService",
    "MockDataProvider",
    "MockMarketDataProvider",
    "MockNewsDataProvider",
    "MockOverseasDataProvider",
    "ProviderRegistry",
    "get_provider_registry",
]
