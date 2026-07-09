import pytest

from datasource.interfaces.market_data import MarketDataProvider
from datasource.interfaces.news_data import NewsDataProvider
from datasource.interfaces.overseas_data import OverseasDataProvider


def test_market_data_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        MarketDataProvider()


def test_news_data_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        NewsDataProvider()


def test_overseas_data_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        OverseasDataProvider()
