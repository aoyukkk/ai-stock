from datasource.mock.overseas_provider import MockOverseasDataProvider


def test_mock_overseas_provider_returns_indices() -> None:
    assert MockOverseasDataProvider().get_global_indices()


def test_mock_overseas_provider_returns_commodities() -> None:
    assert MockOverseasDataProvider().get_commodities()


def test_mock_overseas_provider_returns_fx_rates() -> None:
    assert MockOverseasDataProvider().get_fx_rates()
