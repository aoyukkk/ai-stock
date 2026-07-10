from pathlib import Path

from llm_gateway.base import BaseLLMProvider
from llm_gateway.exceptions import LLMTimeoutError
from llm_gateway.registry import LLMProviderRegistry
from llm_gateway.router import LLMRouter, LLMRouterConfig
from llm_gateway.schemas import LLMMessage, LLMProviderInfo, LLMRequest
from llm_gateway.service import LLMGatewayService


def test_configured_task_routes_resolve_to_flash_and_pro() -> None:
    service = LLMGatewayService()

    expected = {
        "light_screening": ("SIMPLE", "light-screening-default", "deepseek-v4-flash", "disabled", None),
        "news_classification": ("SIMPLE", "light-screening-default", "deepseek-v4-flash", "disabled", None),
        "entity_extraction": ("SIMPLE", "light-screening-default", "deepseek-v4-flash", "disabled", None),
        "risk_review": ("COMPLEX", "risk-high-capability", "deepseek-v4-pro", "enabled", "high"),
        "committee_controller": ("HIGH_IMPACT", "controller-high-capability", "deepseek-v4-pro", "enabled", "max"),
        "black_swan": ("HIGH_IMPACT", "controller-high-capability", "deepseek-v4-pro", "enabled", "max"),
    }
    for task_type, values in expected.items():
        route = service.preview_route(task_type)
        assert (
            route["task_tier"], route["model_alias"], route["model"],
            route["thinking_mode"], route["reasoning_effort"],
        ) == values


def test_new_config_contains_no_deprecated_deepseek_models() -> None:
    text = Path("config/models.yaml").read_text(encoding="utf-8")
    assert "deepseek-chat" not in text
    assert "deepseek-reasoner" not in text


class FailingProProvider(BaseLLMProvider):
    def __init__(self):
        super().__init__("deepseek", True, False)
        self.calls = 0

    def list_models(self):
        return ["deepseek-v4-pro"]

    def health_check(self):
        return LLMProviderInfo(name=self.name, enabled=True, is_mock=False, models=self.list_models(), status="ok")

    def chat(self, request):
        self.calls += 1
        raise LLMTimeoutError("Pro timed out")


class ForbiddenMockProvider(BaseLLMProvider):
    def __init__(self):
        super().__init__("mock", True, True)
        self.calls = 0

    def list_models(self):
        return ["mock-reasoning"]

    def health_check(self):
        return LLMProviderInfo(name=self.name, enabled=True, is_mock=True, models=self.list_models(), status="ok")

    def chat(self, request):
        self.calls += 1
        raise AssertionError("high-impact real route must fail closed")


def test_explicit_real_high_impact_failure_does_not_fallback() -> None:
    deepseek = FailingProProvider()
    mock = ForbiddenMockProvider()
    config = LLMRouterConfig(
        mock_only=False,
        default_provider="mock",
        default_model="mock-reasoning",
        cache_enabled=False,
        cache_ttl_seconds=60,
        routing={},
        fallback={"final": "mock"},
        budgets={},
        aliases={
            "controller-high-capability": {"provider": "deepseek", "model": "deepseek-v4-pro"},
            "mock-reasoning": {"provider": "mock", "model": "mock-reasoning"},
        },
        task_types={"default": "SIMPLE", "black_swan": "HIGH_IMPACT"},
        tier_routing={
            "SIMPLE": {"model_alias": "controller-high-capability", "thinking": "disabled"},
            "HIGH_IMPACT": {"model_alias": "controller-high-capability", "thinking": "enabled", "reasoning_effort": "max"},
        },
    )
    router = LLMRouter(config, registry=LLMProviderRegistry({"deepseek": deepseek, "mock": mock}))
    response = router.chat(
        LLMRequest(
            agent_name="risk_agent",
            task="black_swan",
            messages=[LLMMessage(role="user", content="TEST_ONLY")],
            metadata={"explicit_real_llm_test": True},
            allow_fallback=True,
        )
    )

    assert response.status == "timeout"
    assert response.task_tier == "HIGH_IMPACT"
    assert deepseek.calls == 1
    assert mock.calls == 0
