from __future__ import annotations

from datetime import datetime, timezone

from database.session import get_session
from llm_gateway.service import LLMGatewayService, get_llm_gateway_service
from screening.service import LightScreeningService
from agents.capital_agent import CapitalAgent
from agents.config import AICommitteeConfig, load_ai_committee_config
from agents.context_builder import build_committee_input
from agents.controller_agent import ControllerAgent
from agents.emotion_agent import EmotionAgent
from agents.news_agent import NewsAgent
from agents.overseas_agent import OverseasAgent
from agents.persistence import persist_committee_results
from agents.risk_agent import RiskAgent
from agents.schemas import AgentAnalysisOutput, CommitteeInput, CommitteeRanking, CommitteeStockResult
from agents.technical_agent import TechnicalAgent


AGENT_CLASSES = {
    "technical_agent": TechnicalAgent,
    "news_agent": NewsAgent,
    "capital_agent": CapitalAgent,
    "emotion_agent": EmotionAgent,
    "overseas_agent": OverseasAgent,
    "risk_agent": RiskAgent,
}


class AICommitteeService:
    def __init__(
        self,
        light_screening_service: LightScreeningService | None = None,
        llm_service: LLMGatewayService | None = None,
        config: AICommitteeConfig | None = None,
    ) -> None:
        self.config = config or load_ai_committee_config()
        self.llm_service = llm_service or get_llm_gateway_service()
        self.light_screening_service = light_screening_service or LightScreeningService(llm_service=self.llm_service)

    def run_committee(
        self,
        input_top_n: int | None = None,
        final_top_n: int | None = None,
        persist: bool | None = None,
        session=None,
    ) -> CommitteeRanking:
        self.config.validate()
        requested_input_top_n = int(input_top_n or self.config.input_top_n)
        requested_final_top_n = int(final_top_n or self.config.final_top_n)
        should_persist = self.config.persist_default if persist is None else persist

        light_ranking = self.light_screening_service.run_light_screening(
            top_n=requested_input_top_n,
            persist=False,
        )
        contexts = [build_committee_input(result) for result in light_ranking.results]
        results = [self._analyze_stock(context) for context in contexts]
        results.sort(key=lambda item: item.final_score, reverse=True)
        selected = results[: min(requested_final_top_n, len(results))]
        for index, result in enumerate(selected, start=1):
            result.rank = index

        ranking = CommitteeRanking(
            generated_at=datetime.now(timezone.utc),
            input_count=len(contexts),
            requested_top_n=requested_final_top_n,
            returned_count=len(selected),
            results=selected,
        )

        if should_persist:
            own_session = session is None
            db_session = session or get_session()
            try:
                persist_committee_results(db_session, ranking)
            finally:
                if own_session:
                    db_session.close()
        return ranking

    def config_summary(self) -> dict:
        self.config.validate()
        return self.config.summary()

    def _analyze_stock(self, context: CommitteeInput) -> CommitteeStockResult:
        agent_outputs: list[AgentAnalysisOutput] = []
        for agent_name, agent_config in self.config.enabled_agents.items():
            agent_cls = AGENT_CLASSES[agent_name]
            agent = agent_cls(llm_service=self.llm_service, config=agent_config)
            agent_outputs.append(agent.analyze(context))

        controller = ControllerAgent(llm_service=self.llm_service)
        return controller.analyze(
            context=context,
            agent_outputs=agent_outputs,
            weights=self.config.score_weights,
            thresholds=self.config.recommendation_thresholds,
        )
