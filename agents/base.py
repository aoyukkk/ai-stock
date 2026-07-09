from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from llm_gateway.schemas import LLMMessage, LLMRequest
from llm_gateway.service import LLMGatewayService, get_llm_gateway_service
from agents.config import CommitteeAgentConfig
from agents.context_builder import committee_input_to_prompt_context
from agents.exceptions import AgentParseError
from agents.schemas import AgentAnalysisOutput, CommitteeInput


VALID_DIRECTIONS = {"BUY", "WATCH", "NEUTRAL", "AVOID"}
VALID_ACTIONS = {"ALLOW", "WATCH_ONLY", "BLOCK", "NEED_RECHECK"}


class BaseAgent:
    agent_name = "base_agent"
    task = "committee_base"
    role_description = "Evaluate a compressed A-share committee context."

    def __init__(
        self,
        llm_service: LLMGatewayService | None = None,
        config: CommitteeAgentConfig | None = None,
    ) -> None:
        self.llm_service = llm_service or get_llm_gateway_service()
        self.config = config

    def build_prompt(self, context: CommitteeInput) -> str:
        payload = committee_input_to_prompt_context(context)
        schema = {
            "stock_code": "string",
            "score": "0-100",
            "direction": "BUY|WATCH|NEUTRAL|AVOID",
            "confidence": "0-1",
            "reason": "string",
            "risk_note": "string",
            "action": "ALLOW|WATCH_ONLY|BLOCK|NEED_RECHECK",
        }
        return (
            f"{self.role_description}\n"
            "Use only the compressed context below. Do not invent data, prices, or trade execution commands.\n"
            f"{json.dumps(payload, ensure_ascii=False)}\n"
            "Return strict JSON matching this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )

    def analyze(self, context: CommitteeInput) -> AgentAnalysisOutput:
        prompt = self.build_prompt(context)
        response = self.llm_service.chat(
            LLMRequest(
                agent_name=self.agent_name,
                task=self.task,
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "You are an AI committee specialist. Return strict JSON only. "
                            "This is analysis, not real trading execution."
                        ),
                    ),
                    LLMMessage(role="user", content=prompt),
                ],
                provider=self.config.provider if self.config else None,
                model=self.config.model if self.config else None,
                prompt_version="v0.3-phase7",
                metadata={
                    "structured": True,
                    "committee_context": committee_input_to_prompt_context(context),
                },
            )
        )
        output = self.parse_output(response.content)
        return output.model_copy(
            update={
                "agent_name": self.agent_name,
                "stock_code": context.stock_code,
                "prompt_version": response.prompt_version,
                "model_version": response.model,
                "request_hash": response.request_hash,
            }
        )

    def parse_output(self, content: str) -> AgentAnalysisOutput:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AgentParseError(f"{self.agent_name} output is not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise AgentParseError(f"{self.agent_name} output must be a JSON object.")

        required = {"stock_code", "score", "direction", "confidence", "reason", "risk_note", "action"}
        missing = required - set(parsed)
        if missing:
            raise AgentParseError(f"{self.agent_name} output missing fields: {sorted(missing)}")

        direction = str(parsed.get("direction", "NEUTRAL")).upper()
        if direction not in VALID_DIRECTIONS:
            direction = "NEUTRAL"
        action = str(parsed.get("action", "NEED_RECHECK")).upper()
        if action not in VALID_ACTIONS:
            action = "NEED_RECHECK"

        return AgentAnalysisOutput(
            agent_name=str(parsed.get("agent_name") or self.agent_name),
            stock_code=str(parsed["stock_code"]),
            score=_clamp(parsed["score"], 0, 100),
            direction=direction,  # type: ignore[arg-type]
            confidence=_clamp(parsed["confidence"], 0, 1),
            reason=str(parsed["reason"]),
            risk_note=str(parsed["risk_note"]),
            action=action,  # type: ignore[arg-type]
            raw_output=parsed,
        )


def _clamp(value: Any, lower: int, upper: int) -> Decimal:
    numeric = Decimal(str(value))
    return max(Decimal(lower), min(Decimal(upper), numeric)).quantize(Decimal("0.0001"))
