import pytest

from datasource.ths.adapter import THSMarketDataProvider


def test_ths_market_data_provider_imports() -> None:
    assert THSMarketDataProvider()


def test_ths_market_data_provider_methods_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        THSMarketDataProvider().get_stock_list()
