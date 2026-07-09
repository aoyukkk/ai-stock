from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from datasource.akshare_provider import AKShareProvider
from datasource.base import BaseDataProvider, MarketDataProvider, NewsDataProvider, OverseasDataProvider
from datasource.exceptions import ProviderNotFoundError
from datasource.mock_provider import MockDataProvider
from datasource.news_provider import RealNewsProvider
from datasource.overseas_provider import RealOverseasProvider
from datasource.ths_provider import THSProvider
from datasource.tushare_provider import TushareProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, BaseDataProvider] = {}

    def register_provider(self, provider: BaseDataProvider) -> None:
        self._providers[provider.name] = provider

    def get_provider(self, name: str) -> BaseDataProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ProviderNotFoundError(f"Provider is not registered: {name}") from exc

    def list_providers(self) -> list[BaseDataProvider]:
        return list(self._providers.values())

    def get_enabled_providers(self) -> list[BaseDataProvider]:
        return [
            provider
            for provider in self._providers.values()
            if provider.enabled and provider.health_check().healthy
        ]

    def get_default_market_provider(self) -> MarketDataProvider:
        provider = self.get_provider("mock")
        if not isinstance(provider, MarketDataProvider):
            raise ProviderNotFoundError("Default market provider is unavailable.")
        return provider

    def get_default_news_provider(self) -> NewsDataProvider:
        provider = self.get_provider("mock")
        if not isinstance(provider, NewsDataProvider):
            raise ProviderNotFoundError("Default news provider is unavailable.")
        return provider

    def get_default_overseas_provider(self) -> OverseasDataProvider:
        provider = self.get_provider("mock")
        if not isinstance(provider, OverseasDataProvider):
            raise ProviderNotFoundError("Default overseas provider is unavailable.")
        return provider


def create_default_registry() -> ProviderRegistry:
    config = _load_data_source_config()
    registry = ProviderRegistry()
    registry.register_provider(MockDataProvider(enabled=_mock_enabled(config)))
    registry.register_provider(THSProvider())
    registry.register_provider(TushareProvider())
    registry.register_provider(AKShareProvider())
    registry.register_provider(RealNewsProvider(name="ths_news"))
    registry.register_provider(RealNewsProvider(name="jinshi"))
    registry.register_provider(RealOverseasProvider(name="okx"))
    return registry


@lru_cache(maxsize=1)
def get_provider_registry() -> ProviderRegistry:
    return create_default_registry()


def _load_data_source_config() -> dict[str, Any]:
    config_path = Path(__file__).resolve().parents[1] / "config" / "data_sources.yaml"
    if not config_path.exists():
        return {"mock": {"enabled": True}}

    with config_path.open("r", encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file) or {}
    return loaded.get("data_sources", {}) if isinstance(loaded, dict) else {}


def _mock_enabled(config: dict[str, Any]) -> bool:
    if not config:
        return True
    if _provider_enabled(config, "mock") is not None:
        return bool(_provider_enabled(config, "mock"))
    return True


def _provider_enabled(obj: Any, provider_name: str) -> bool | None:
    if isinstance(obj, dict):
        if obj.get("provider") == provider_name:
            return bool(obj.get("enabled", False))
        for value in obj.values():
            found = _provider_enabled(value, provider_name)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _provider_enabled(item, provider_name)
            if found is not None:
                return found
    return None
