from __future__ import annotations

import json
from decimal import Decimal

from llm_gateway.schemas import LLMMessage, LLMRequest
from llm_gateway.service import LLMGatewayService, get_llm_gateway_service
from agents.config import CommitteeAgentConfig
from agents.context_builder import committee_input_to_prompt_context
from agents.schemas import (
    AgentAnalysisOutput,
    CommitteeInput,
    CommitteeStockResult,
    Recommendation,
    RiskLevel,
)
from agents.base import AGENT_RESPONSE_SCHEMA


class ControllerAgent:
    agent_name = "controller_agent"
    task = "committee_controller"

    def __init__(
        self,
        llm_service: LLMGatewayService | None = None,
        config: CommitteeAgentConfig | None = None,
    ) -> None:
        self.llm_service = llm_service or get_llm_gateway_service()
        self.config = config

    def analyze(
        self,
        context: CommitteeInput,
        agent_outputs: list[AgentAnalysisOutput],
        weights: dict[str, Decimal],
        thresholds: dict[str, Decimal],
    ) -> CommitteeStockResult:
        scores = _scores_by_agent(agent_outputs)
        final_score = _weighted_score(scores, weights)
        risk_output = scores.get("risk_agent")
        recommendation = _recommendation(final_score, thresholds, risk_output)
        risk_level = _risk_level(risk_output)
        confidence = _average_confidence(agent_outputs)

        response = self.llm_service.chat(
            LLMRequest(
                agent_name=self.agent_name,
                task=self.task,
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "You are Controller Agent. Summarize structured specialist outputs. "
                            "Do not generate order prices or trading execution commands."
                        ),
                    ),
                    LLMMessage(role="user", content=self.build_prompt(context, agent_outputs, final_score, recommendation)),
                ],
                model_alias=self.config.model_alias if self.config else "mock-reasoning",
                prompt_version="v0.3-phase7",
                metadata={
                    "structured": True,
                    "committee_context": committee_input_to_prompt_context(context),
                    "agent_outputs": [output.model_dump(mode="json") for output in agent_outputs],
                    "controller_result": {
                        "final_score": str(final_score),
                        "recommendation": recommendation,
                        "risk_level": risk_level,
                    },
                },
                response_schema=AGENT_RESPONSE_SCHEMA,
                json_mode=True,
            )
        )
        if response.status != "ok":
            recommendation = "BLOCKED"
            risk_level = "HIGH"
            confidence = Decimal("0")
            explanation_reason = "Controller model unavailable; manual review is required."
            risk_note = f"Safe failure status: {response.status}"
        else:
            explanation = _parse_controller_explanation(response.content)
            explanation_reason = explanation.reason
            risk_note = explanation.risk_note or (risk_output.risk_note if risk_output else "")

        return CommitteeStockResult(
            stock_code=context.stock_code,
            stock_name=context.stock_name,
            industry=context.industry,
            quant_score=context.quant_total_score,
            light_score=context.final_light_score,
            technical_score=_agent_score(scores, "technical_agent"),
            news_score=_agent_score(scores, "news_agent"),
            capital_score=_agent_score(scores, "capital_agent"),
            emotion_score=_agent_score(scores, "emotion_agent"),
            overseas_score=_agent_score(scores, "overseas_agent"),
            risk_score=_agent_score(scores, "risk_agent"),
            final_score=final_score,
            recommendation=recommendation,  # type: ignore[arg-type]
            risk_level=risk_level,  # type: ignore[arg-type]
            confidence=confidence,
            controller_reason=explanation_reason,
            risk_note=risk_note,
            agent_outputs=agent_outputs,
            prompt_version=response.prompt_version,
            model_version=response.model,
        )

    def build_prompt(
        self,
        context: CommitteeInput,
        agent_outputs: list[AgentAnalysisOutput],
        final_score: Decimal,
        recommendation: str,
    ) -> str:
        payload = {
            "stock": committee_input_to_prompt_context(context),
            "agent_outputs": [output.model_dump(mode="json") for output in agent_outputs],
            "rule_final_score": str(final_score),
            "rule_recommendation": recommendation,
        }
        return (
            "Summarize these committee outputs as strict JSON with stock_code, score, direction, "
            "confidence, reason, risk_note, action. Do not create prices or execution commands.\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )


def _scores_by_agent(agent_outputs: list[AgentAnalysisOutput]) -> dict[str, AgentAnalysisOutput]:
    return {output.agent_name: output for output in agent_outputs}


def _weighted_score(scores: dict[str, AgentAnalysisOutput], weights: dict[str, Decimal]) -> Decimal:
    total = Decimal("0")
    for agent_name, weight in weights.items():
        output = scores.get(agent_name)
        if output is not None:
            total += output.score * weight
    return _clamp(total)


def _recommendation(
    final_score: Decimal,
    thresholds: dict[str, Decimal],
    risk_output: AgentAnalysisOutput | None,
) -> Recommendation:
    if risk_output and risk_output.action == "BLOCK":
        return "BLOCKED"
    if final_score >= thresholds["strong_watch"]:
        return "STRONG_WATCH"
    if final_score >= thresholds["watch"]:
        return "WATCH"
    if final_score >= thresholds["neutral"]:
        return "NEUTRAL"
    return "AVOID"


def _risk_level(risk_output: AgentAnalysisOutput | None) -> RiskLevel:
    if risk_output is None:
        return "MEDIUM"
    if risk_output.action == "BLOCK" and risk_output.score < 40:
        return "BLACK_SWAN"
    if risk_output.score >= 80:
        return "LOW"
    if risk_output.score >= 60:
        return "MEDIUM"
    return "HIGH"


def _average_confidence(agent_outputs: list[AgentAnalysisOutput]) -> Decimal:
    if not agent_outputs:
        return Decimal("0")
    total = sum((output.confidence for output in agent_outputs), Decimal("0"))
    return (total / Decimal(len(agent_outputs))).quantize(Decimal("0.0001"))


def _agent_score(scores: dict[str, AgentAnalysisOutput], name: str) -> Decimal:
    output = scores.get(name)
    return output.score if output else Decimal("0")


def _parse_controller_explanation(content: str) -> AgentAnalysisOutput:
    from agents.base import BaseAgent

    parser = BaseAgent()
    parser.agent_name = "controller_agent"
    return parser.parse_output(content)


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), value)).quantize(Decimal("0.0001"))
