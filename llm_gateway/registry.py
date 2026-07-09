from __future__ import annotations

from functools import lru_cache
from typing import Any

from backend.core.config import get_app_config
from llm_gateway.base import BaseLLMProvider
from llm_gateway.exceptions import LLMProviderNotFoundError
from llm_gateway.mock_provider import MockLLMProvider
from llm_gateway.placeholder_provider import PlaceholderLLMProvider


class LLMProviderRegistry:
    def __init__(self, providers: dict[str, BaseLLMProvider] | None = None) -> None:
        self._providers = providers or {}

    def register_provider(self, provider: BaseLLMProvider) -> None:
        self._providers[provider.name] = provider

    def get_provider(self, name: str) -> BaseLLMProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise LLMProviderNotFoundError(f"LLM provider is not registered: {name}") from exc

    def list_providers(self) -> list[BaseLLMProvider]:
        return list(self._providers.values())

    def list_provider_info(self) -> list[dict[str, Any]]:
        return [
            provider.health_check().model_dump(mode="json")
            for provider in self.list_providers()
        ]


def create_default_registry() -> LLMProviderRegistry:
    models_config = _models_config()
    providers_config = models_config.get("llm", {}).get("providers", {})
    registry = LLMProviderRegistry()
    registry.register_provider(
        MockLLMProvider(enabled=bool(providers_config.get("mock", {}).get("enabled", True)))
    )
    registry.register_provider(
        PlaceholderLLMProvider("deepseek", ["deepseek-v4-flash"])
    )
    registry.register_provider(
        PlaceholderLLMProvider("openai", ["gpt-5.5"])
    )
    registry.register_provider(
        PlaceholderLLMProvider("claude", ["claude"])
    )
    registry.register_provider(
        PlaceholderLLMProvider("qwen", ["qwen"])
    )
    return registry


@lru_cache(maxsize=1)
def get_llm_provider_registry() -> LLMProviderRegistry:
    return create_default_registry()


def _models_config() -> dict[str, Any]:
    return get_app_config().config_files.get("models", {})
