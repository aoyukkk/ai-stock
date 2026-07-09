from llm_gateway.cache import InMemoryLLMCache
from llm_gateway.registry import create_default_registry
from llm_gateway.router import LLMRouter, LLMRouterConfig, build_request_hash
from llm_gateway.schemas import LLMMessage, LLMRequest


def make_config(mock_only: bool = True) -> LLMRouterConfig:
    return LLMRouterConfig(
        mock_only=mock_only,
        default_provider="mock",
        default_model="mock-chat",
        cache_enabled=True,
        cache_ttl_seconds=1800,
        routing={"default": {"provider": "mock", "model": "mock-chat"}},
        fallback={"primary": "mock", "secondary": "mock", "final": "mock"},
        budgets={"daily_token_budget": 1800000, "warning_percent": 80},
    )


def make_request(provider: str | None = None) -> LLMRequest:
    return LLMRequest(
        agent_name="technical_agent",
        task="test",
        provider=provider,
        messages=[LLMMessage(role="user", content="hello")],
    )


def test_router_routes_chat_to_mock_and_caches_second_call() -> None:
    router = LLMRouter(
        config=make_config(mock_only=True),
        registry=create_default_registry(),
        cache=InMemoryLLMCache(),
    )

    first = router.chat(make_request())
    second = router.chat(make_request())

    assert first.provider == "mock"
    assert first.cached is False
    assert second.provider == "mock"
    assert second.cached is True
    assert first.request_hash == second.request_hash


def test_specified_real_provider_still_routes_to_mock_when_mock_only() -> None:
    router = LLMRouter(
        config=make_config(mock_only=True),
        registry=create_default_registry(),
        cache=InMemoryLLMCache(),
    )

    response = router.chat(make_request(provider="deepseek"))

    assert response.provider == "mock"
    assert response.status == "ok"


def test_fallback_to_mock_when_real_provider_disabled() -> None:
    router = LLMRouter(
        config=make_config(mock_only=False),
        registry=create_default_registry(),
        cache=InMemoryLLMCache(),
    )

    response = router.chat(make_request(provider="openai"))

    assert response.provider == "mock"
    assert response.status == "ok"


def test_request_hash_is_stable() -> None:
    request = make_request()

    assert build_request_hash(request) == build_request_hash(request)
