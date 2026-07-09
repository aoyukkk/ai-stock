from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from llm_gateway.exceptions import PromptNotFoundError
from llm_gateway.schemas import PromptTemplate


DEFAULT_PROMPTS = {
    "technical_agent": "You are a mock technical analyst. Input: {input}",
    "news_agent": "You are a mock news analyst. Input: {input}",
    "capital_agent": "You are a mock capital-flow analyst. Input: {input}",
    "emotion_agent": "You are a mock market-emotion analyst. Input: {input}",
    "overseas_agent": "You are a mock overseas-market analyst. Input: {input}",
    "risk_agent": "You are a mock risk reviewer. Input: {input}",
    "controller_agent": "You are a mock controller. Input: {input}",
    "daily_review_agent": "You are a mock daily reviewer. Input: {input}",
}


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class PromptManager:
    _prompts: dict[str, dict[str, PromptTemplate]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for agent_name, template in DEFAULT_PROMPTS.items():
            self.register_prompt(
                agent_name=agent_name,
                version="v0.3-phase5",
                template=template,
                description="Phase 5 placeholder prompt.",
                is_active=True,
            )

    def register_prompt(
        self,
        agent_name: str,
        version: str,
        template: str,
        description: str = "",
        is_active: bool = True,
    ) -> PromptTemplate:
        agent_prompts = self._prompts.setdefault(agent_name, {})
        if is_active:
            agent_prompts.update(
                {
                    item_version: item.model_copy(update={"is_active": False})
                    for item_version, item in agent_prompts.items()
                }
            )
        prompt = PromptTemplate(
            agent_name=agent_name,
            version=version,
            template=template,
            description=description,
            is_active=is_active,
            content_hash=content_hash(template),
        )
        agent_prompts[version] = prompt
        return prompt

    def get_active_prompt(self, agent_name: str) -> PromptTemplate:
        agent_prompts = self._prompts.get(agent_name, {})
        for prompt in agent_prompts.values():
            if prompt.is_active:
                return prompt
        raise PromptNotFoundError(f"No active prompt for agent: {agent_name}")

    def render_prompt(self, agent_name: str, variables: dict[str, Any]) -> str:
        prompt = self.get_active_prompt(agent_name)
        return prompt.template.format(**variables)
