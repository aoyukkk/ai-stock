from decimal import Decimal

from agents.config import load_ai_committee_config
from agents.context_builder import build_committee_input
from agents.capital_agent import CapitalAgent
from agents.emotion_agent import EmotionAgent
from agents.news_agent import NewsAgent
from agents.overseas_agent import OverseasAgent
from agents.risk_agent import RiskAgent
from agents.technical_agent import TechnicalAgent
from llm_gateway.service import LLMGatewayService
from screening.schemas import LightScreeningResult


def make_context():
    return build_committee_input(
        LightScreeningResult(
            stock_code="000001",
            stock_name="Ping An Bank",
            industry="bank",
            quant_rank=1,
            quant_total_score=Decimal("76"),
            llm_score=Decimal("72"),
            final_light_score=Decimal("70"),
            rank=1,
            direction="WATCH",
            confidence=Decimal("0.76"),
            should_keep=True,
            reason="compressed light reason",
            risk_note="compressed risk note",
        )
    )


def test_individual_agents_return_structured_outputs_through_gateway() -> None:
    llm_service = LLMGatewayService()
    config = load_ai_committee_config()
    context = make_context()
    agent_classes = [
        TechnicalAgent,
        NewsAgent,
        CapitalAgent,
        EmotionAgent,
        OverseasAgent,
        RiskAgent,
    ]

    outputs = [
        agent_cls(llm_service=llm_service, config=config.agents[agent_cls.agent_name]).analyze(context)
        for agent_cls in agent_classes
    ]

    assert llm_service.get_usage_summary()["call_count"] == len(agent_classes)
    for output in outputs:
        assert output.stock_code == "000001"
        assert 0 <= output.score <= 100
        assert 0 <= output.confidence <= 1
        assert output.direction in {"BUY", "WATCH", "NEUTRAL", "AVOID"}
        assert output.action in {"ALLOW", "WATCH_ONLY", "BLOCK", "NEED_RECHECK"}
        assert output.model_version in {"mock-chat", "mock-fast", "mock-reasoning"}
        assert output.request_hash


def test_individual_agents_are_configured_for_mock_provider_only() -> None:
    config = load_ai_committee_config()

    assert config.mock_only is True
    assert all(agent.provider == "mock" for agent in config.agents.values())
