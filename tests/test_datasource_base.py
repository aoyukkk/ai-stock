from datetime import date

import pytest

from datasource.base import BaseDataProvider, MarketDataProvider, NewsDataProvider, OverseasDataProvider
from datasource.mock_provider import MockDataProvider
from datasource.schemas import ProviderStatus


def test_provider_status_can_be_created() -> None:
    status = ProviderStatus(
        name="mock",
        provider_type="market_news_overseas",
        enabled=True,
        is_mock=True,
        healthy=True,
        status="ok",
    )

    assert status.name == "mock"
    assert status.is_mock is True


def test_base_interfaces_exist() -> None:
    assert hasattr(BaseDataProvider, "health_check")
    assert hasattr(BaseDataProvider, "get_provider_info")
    assert hasattr(MarketDataProvider, "get_stock_list")
    assert hasattr(MarketDataProvider, "get_realtime_quotes")
    assert hasattr(MarketDataProvider, "get_kline")
    assert hasattr(NewsDataProvider, "get_latest_news")
    assert hasattr(NewsDataProvider, "search_news")
    assert hasattr(OverseasDataProvider, "get_overseas_indices")


def test_mock_provider_implements_required_methods() -> None:
    provider = MockDataProvider()

    assert provider.health_check().status == "ok"
    assert provider.get_stock_list()
    assert provider.get_realtime_quotes(["000001"])
    assert provider.get_kline("000001", date(2026, 1, 1), date(2026, 1, 3))
    assert provider.get_finance("000001")
    assert provider.get_capital_flow("000001")
    assert provider.get_market_emotion()
    assert provider.get_limit_price("000001", date(2026, 1, 5))
    assert provider.get_pre_market_auction("000001", date(2026, 1, 5))
    assert provider.get_latest_news()
    assert provider.get_overseas_indices()


def test_base_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        BaseDataProvider("base", "base")
