import pytest

from datasource.exceptions import NOT_IMPLEMENTED_MESSAGE, ProviderNotFoundError, ProviderNotImplementedError
from datasource.registry import create_default_registry


def test_registry_default_registers_mock_provider() -> None:
    registry = create_default_registry()
    provider = registry.get_provider("mock")

    assert provider.name == "mock"
    assert provider.enabled is True
    assert provider.is_mock is True


def test_default_market_news_overseas_providers_are_mock() -> None:
    registry = create_default_registry()

    assert registry.get_default_market_provider().name == "mock"
    assert registry.get_default_news_provider().name == "mock"
    assert registry.get_default_overseas_provider().name == "mock"


def test_unregistered_provider_raises_clear_error() -> None:
    registry = create_default_registry()

    with pytest.raises(ProviderNotFoundError, match="not registered"):
        registry.get_provider("missing")


def test_real_providers_are_manual_debug_or_disabled_by_default() -> None:
    registry = create_default_registry()
    statuses = {provider.name: provider.health_check() for provider in registry.list_providers()}

    assert statuses["ths"].enabled is False
    assert statuses["tushare"].enabled is True
    assert statuses["tushare"].status == "manual_debug"
    assert statuses["baostock"].enabled is True
    assert statuses["baostock"].status == "manual_debug"
    assert statuses["akshare"].enabled is False
    assert statuses["ths_news"].enabled is False
    assert statuses["okx"].enabled is False


def test_real_provider_placeholder_returns_clear_error() -> None:
    registry = create_default_registry()
    provider = registry.get_provider("ths")

    with pytest.raises(ProviderNotImplementedError, match=NOT_IMPLEMENTED_MESSAGE):
        provider.get_stock_list()
