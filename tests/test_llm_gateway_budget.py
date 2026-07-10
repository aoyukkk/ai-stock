from llm_gateway.schemas import LLMMessage, LLMRequest
from llm_gateway.service import LLMGatewayService


def _request(*, allow_fallback: bool = True) -> LLMRequest:
    return LLMRequest(
        agent_name="gateway_connectivity_test",
        task="connectivity_test",
        model_alias="light-screening-default",
        messages=[LLMMessage(role="user", content="Return JSON")],
        allow_fallback=allow_fallback,
        metadata={"structured": True, "explicit_real_llm_test": True},
    )


def test_budget_warning_at_eighty_percent() -> None:
    service = LLMGatewayService()
    service.config.budgets.update({"daily_token_budget": 100, "warning_percent": 80, "limit_percent": 100})
    service.usage.total_tokens = 80

    service.chat(_request())

    assert service.usage.budget_warning is True
    assert service.usage.budget_exhausted is False


def test_budget_hard_limit_falls_back_to_mock() -> None:
    service = LLMGatewayService()
    service.config.budgets.update({"daily_token_budget": 100, "warning_percent": 80, "limit_percent": 100})
    service.usage.total_tokens = 100

    response = service.chat(_request(allow_fallback=True))

    assert response.provider == "mock"
    assert response.status == "ok"
    assert service.usage.budget_exhausted is True


def test_budget_hard_limit_blocks_non_fallback_real_test() -> None:
    service = LLMGatewayService()
    service.config.budgets.update({"daily_token_budget": 100, "warning_percent": 80, "limit_percent": 100})
    service.usage.total_tokens = 100

    response = service.chat(_request(allow_fallback=False))

    assert response.status == "budget_exceeded"
    assert response.provider == "deepseek"
