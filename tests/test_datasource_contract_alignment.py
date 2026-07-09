from datasource.base import MarketDataProvider as LegacyMarketDataProvider
from datasource.interfaces.market_data import MarketDataProvider as CanonicalMarketDataProvider
from datasource.interfaces.news_data import NewsDataProvider as CanonicalNewsDataProvider
from datasource.interfaces.overseas_data import OverseasDataProvider as CanonicalOverseasDataProvider
from datasource.mock_provider import MockDataProvider
from datasource.schemas import CanonicalMarketStockInfo, ProviderStatus, StockInfo


def test_canonical_interfaces_can_be_imported() -> None:
    assert hasattr(CanonicalMarketDataProvider, "get_stock_list")
    assert hasattr(CanonicalNewsDataProvider, "get_latest_news")
    assert hasattr(CanonicalOverseasDataProvider, "get_global_indices")


def test_legacy_datasource_modules_remain_importable() -> None:
    assert hasattr(LegacyMarketDataProvider, "get_realtime_quotes")
    assert ProviderStatus(name="mock", provider_type="mock", enabled=True, is_mock=True, healthy=True, status="ok")
    assert StockInfo(stock_code="000001", name="Ping An Bank", market="SZ")
    assert CanonicalMarketStockInfo(code="000001", name="Ping An Bank", market="SZ")


def test_legacy_mock_provider_is_compatible_with_canonical_interface() -> None:
    provider = MockDataProvider()

    assert isinstance(provider, LegacyMarketDataProvider)
    assert isinstance(provider, CanonicalMarketDataProvider)
    assert provider.get_stock_list()
    assert provider.get_realtime("000001").stock_code == "000001"
