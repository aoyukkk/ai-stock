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


def test_real_deepseek_and_other_placeholder_providers_are_registered() -> None:
    registry = create_default_registry()

    deepseek = registry.get_provider("deepseek")
    assert deepseek.enabled is True
    assert deepseek.is_mock is False
    assert deepseek.health_check().status in {"configured", "not_configured"}

    for provider_name in ["openai", "claude", "qwen"]:
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
