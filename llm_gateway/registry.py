from __future__ import annotations

from functools import lru_cache
from typing import Any

from backend.core.config_manager import ConfigManager
from llm_gateway.base import BaseLLMProvider
from llm_gateway.deepseek_provider import DeepSeekLLMProvider
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
    aliases = models_config.get("llm", {}).get("aliases", {})
    deepseek_models = sorted({
        str(value.get("model"))
        for value in aliases.values()
        if isinstance(value, dict) and value.get("provider") == "deepseek" and value.get("model")
    })
    deepseek_config = providers_config.get("deepseek", {})
    registry.register_provider(
        DeepSeekLLMProvider(
            enabled=bool(deepseek_config.get("enabled", False)),
            api_key_env=str(deepseek_config.get("api_key_env") or "DEEPSEEK_API_KEY"),
            models=deepseek_models,
            base_url=str(deepseek_config.get("base_url") or "https://api.deepseek.com"),
            connect_timeout_seconds=float(deepseek_config.get("connect_timeout_seconds", 5)),
            read_timeout_seconds=float(deepseek_config.get("read_timeout_seconds", 60)),
            max_attempts=int(deepseek_config.get("max_attempts", 3)),
            retry_backoff_seconds=float(deepseek_config.get("retry_backoff_seconds", 0.5)),
        )
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
    return ConfigManager().get_llm_gateway_config()
