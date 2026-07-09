import json

import pytest

from llm_gateway.exceptions import LLMProviderUnavailableError, PROVIDER_DISABLED_MESSAGE
from llm_gateway.registry import create_default_registry


def test_default_registry_registers_mock_provider() -> None:
    registry = create_default_registry()
    provider = registry.get_provider("mock")

    assert provider.name == "mock"
    assert provider.enabled is True
    assert provider.is_mock is True


def test_placeholder_providers_are_disabled_and_not_implemented() -> None:
    registry = create_default_registry()

    for provider_name in ["deepseek", "openai", "claude", "qwen"]:
        provider = registry.get_provider(provider_name)
        assert provider.enabled is False
        assert provider.health_check().status == "not_implemented"
        with pytest.raises(LLMProviderUnavailableError, match=PROVIDER_DISABLED_MESSAGE):
            provider.chat(None)  # type: ignore[arg-type]


def test_list_providers_does_not_expose_secrets() -> None:
    registry = create_default_registry()
    payload = json.dumps(registry.list_provider_info(), ensure_ascii=False).lower()

    for forbidden in ["api_key", "password", "secret", "username", "deepseek_api_key"]:
        assert forbidden not in payload
