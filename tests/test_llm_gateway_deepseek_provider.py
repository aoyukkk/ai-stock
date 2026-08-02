import json

import httpx
import pytest

from llm_gateway.deepseek_provider import DeepSeekLLMProvider
from llm_gateway.exceptions import (
    LLMAuthenticationError,
    LLMProviderNotConfiguredError,
    LLMProviderResponseError,
    LLMTimeoutError,
)
from llm_gateway.schemas import LLMMessage, LLMRequest


def _request() -> LLMRequest:
    return LLMRequest(
        agent_name="gateway_test",
        task="json_test",
        model="deepseek-v4-flash",
        messages=[LLMMessage(role="user", content="return json")],
        json_mode=True,
        max_tokens=64,
    )


def _provider(monkeypatch, handler, **overrides) -> DeepSeekLLMProvider:
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "test-secret-value")
    options = {
        "enabled": True,
        "api_key_env": "TEST_DEEPSEEK_KEY",
        "models": ["deepseek-v4-flash"],
        "transport": httpx.MockTransport(handler),
        "max_attempts": 3,
        "retry_backoff_seconds": 0,
    }
    options.update(overrides)
    return DeepSeekLLMProvider(**options)


def test_deepseek_request_contract_and_response_normalization(monkeypatch) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "safe-id",
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": '{"result":"OK"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            },
        )

    response = _provider(monkeypatch, handler).chat(_request())

    request = captured["request"]
    assert request.url == httpx.URL("https://api.deepseek.com/chat/completions")
    assert request.headers["authorization"] == "Bearer test-secret-value"
    assert captured["body"]["model"] == "deepseek-v4-flash"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert request.extensions["timeout"]["connect"] == 5.0
    assert request.extensions["timeout"]["read"] == 60.0
    assert response.parsed_json == {"result": "OK"}
    assert response.total_tokens == 16
    assert response.finish_reason == "stop"
    assert response.cost_usd is None
    assert "authorization" not in json.dumps(response.raw_response_metadata).lower()


def test_deepseek_missing_key_is_safe_and_does_not_call_network(monkeypatch) -> None:
    monkeypatch.delenv("TEST_DEEPSEEK_KEY", raising=False)
    provider = DeepSeekLLMProvider(
        enabled=True,
        api_key_env="TEST_DEEPSEEK_KEY",
        models=["deepseek-v4-flash"],
        transport=httpx.MockTransport(lambda request: pytest.fail("network must not be called")),
    )

    assert provider.health_check().status == "not_configured"
    with pytest.raises(LLMProviderNotConfiguredError):
        provider.chat(_request())


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_are_not_retried_or_leaked(monkeypatch, status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, request=request, text="Bearer test-secret-value")

    with pytest.raises(LLMAuthenticationError) as exc_info:
        _provider(monkeypatch, handler).chat(_request())

    assert calls == 1
    assert "test-secret-value" not in str(exc_info.value)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_http_failures_have_bounded_retry(monkeypatch, status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(status, request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": '{"result":"OK"}'}, "finish_reason": "stop"}],
                "usage": {},
            },
        )

    assert _provider(monkeypatch, handler).chat(_request()).status == "ok"
    assert calls == 3


def test_timeout_has_bounded_retry(monkeypatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("secret test-secret-value", request=request)

    with pytest.raises(LLMTimeoutError) as exc_info:
        _provider(monkeypatch, handler, max_attempts=2).chat(_request())
    assert calls == 2
    assert "test-secret-value" not in str(exc_info.value)


def test_malformed_provider_response_is_rejected(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"choices": []})

    with pytest.raises(LLMProviderResponseError, match="malformed"):
        _provider(monkeypatch, handler).chat(_request())


def test_http_400_surfaces_redacted_provider_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            request=request,
            json={
                "error": {
                    "code": "invalid_request",
                    "message": "bad field; Bearer test-secret-value",
                }
            },
        )

    with pytest.raises(LLMProviderResponseError) as exc_info:
        _provider(monkeypatch, handler).chat(_request())

    assert "invalid_request" in str(exc_info.value)
    assert "test-secret-value" not in str(exc_info.value)


def test_thinking_parameters_are_explicit_and_temperature_is_omitted(monkeypatch) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "deepseek-v4-pro",
                "choices": [{
                    "message": {"content": '{"result":"OK"}', "reasoning_content": "must-not-persist"},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 12,
                    "prompt_cache_hit_tokens": 2,
                    "prompt_cache_miss_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 16,
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
        )

    request = _request().model_copy(
        update={
            "model": "deepseek-v4-pro",
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
            "temperature": 0.7,
        }
    )
    response = _provider(monkeypatch, handler).chat(request)

    assert captured["thinking"] == {"type": "enabled"}
    assert captured["reasoning_effort"] == "high"
    assert "temperature" not in captured
    assert response.input_cache_hit_tokens == 2
    assert response.input_cache_miss_tokens == 10
    assert "reasoning_content" not in json.dumps(response.model_dump()).lower()
    assert response.raw_response_metadata["reasoning_tokens"] == 3


def test_disabled_thinking_is_sent_explicitly_without_reasoning_effort(monkeypatch) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "deepseek-v4-pro",
                "choices": [{"message": {"content": '{"result":"OK"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        )

    request = _request().model_copy(
        update={
            "model": "deepseek-v4-pro",
            "thinking_mode": "disabled",
            "reasoning_effort": None,
            "temperature": 0.2,
        }
    )
    _provider(monkeypatch, handler).chat(request)

    assert captured["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in captured
    assert captured["temperature"] == 0.2


def test_remote_model_list_is_normalized(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/models"
        return httpx.Response(
            200,
            request=request,
            json={
                "object": "list",
                "data": [
                    {"id": "deepseek-v4-pro", "object": "model", "owned_by": "deepseek"},
                    {"id": "deepseek-v4-flash", "object": "model", "owned_by": "deepseek"},
                ],
            },
        )

    assert _provider(monkeypatch, handler).fetch_available_models() == [
        "deepseek-v4-flash", "deepseek-v4-pro"
    ]
