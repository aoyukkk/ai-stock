from llm_gateway.mock_provider import MockLLMProvider
from llm_gateway.schemas import LLMMessage, LLMRequest


def test_mock_provider_chat_returns_stable_local_response() -> None:
    provider = MockLLMProvider()
    response = provider.chat(
        LLMRequest(
            agent_name="technical_agent",
            task="test",
            messages=[LLMMessage(role="user", content="hello")],
            model="mock-chat",
        )
    )

    assert response.provider == "mock"
    assert response.model == "mock-chat"
    assert "Mock LLM response" in response.content
    assert response.input_tokens >= 0
    assert response.output_tokens >= 0
    assert response.total_tokens == response.input_tokens + response.output_tokens
    assert response.cost_usd == 0
    assert response.status == "ok"


def test_mock_provider_structured_task_returns_structured_output() -> None:
    provider = MockLLMProvider()
    response = provider.chat(
        LLMRequest(
            agent_name="risk_agent",
            task="risk_review",
            messages=[LLMMessage(role="user", content="风险检查")],
            model="mock-reasoning",
        )
    )

    assert response.structured_output is not None
    assert response.structured_output["mock"] is True
    assert response.structured_output["data_conflict"] is False
