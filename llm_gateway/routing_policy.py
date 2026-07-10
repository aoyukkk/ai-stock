from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from llm_gateway.exceptions import LLMProviderUnavailableError


class TaskTier(str, Enum):
    SIMPLE = "SIMPLE"
    COMPLEX = "COMPLEX"
    HIGH_IMPACT = "HIGH_IMPACT"


@dataclass(frozen=True)
class RouteDecision:
    task_type: str
    task_tier: TaskTier
    model_alias: str
    provider: str
    model: str
    thinking_mode: str
    reasoning_effort: str | None
    temperature: float | None
    max_tokens: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "task_tier": self.task_tier.value,
            "model_alias": self.model_alias,
            "provider": self.provider,
            "model": self.model,
            "thinking_mode": self.thinking_mode,
            "reasoning_effort": self.reasoning_effort,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


class ModelRoutingPolicy:
    def __init__(
        self,
        *,
        aliases: dict[str, dict[str, Any]],
        task_types: dict[str, str],
        tiers: dict[str, dict[str, Any]],
        deprecated_models: set[str] | None = None,
    ) -> None:
        self.aliases = aliases
        self.task_types = {str(key): str(value).upper() for key, value in task_types.items()}
        self.tiers = {str(key).upper(): value for key, value in tiers.items()}
        self.deprecated_models = deprecated_models or {"deepseek-chat", "deepseek-reasoner"}

    def resolve(self, task_type: str, model_alias: str | None = None) -> RouteDecision:
        normalized_task = str(task_type or "default")
        tier_name = self.task_types.get(normalized_task, self.task_types.get("default", TaskTier.SIMPLE.value))
        try:
            tier = TaskTier(tier_name)
        except ValueError as exc:
            raise LLMProviderUnavailableError(f"Unknown task tier for {normalized_task}: {tier_name}") from exc
        tier_config = self.tiers.get(tier.value, {})
        alias = str(model_alias or tier_config.get("model_alias") or "")
        if not alias:
            raise LLMProviderUnavailableError(f"No model alias configured for task tier: {tier.value}")
        try:
            alias_config = self.aliases[alias]
        except KeyError as exc:
            raise LLMProviderUnavailableError(f"Unknown model alias: {alias}") from exc
        model = str(alias_config.get("model") or "")
        if model in self.deprecated_models:
            raise LLMProviderUnavailableError(f"Deprecated model is not allowed for new routing: {model}")
        provider = str(alias_config.get("provider") or "")
        if not provider or not model:
            raise LLMProviderUnavailableError(f"Incomplete model alias configuration: {alias}")
        thinking_mode = str(tier_config.get("thinking") or alias_config.get("thinking") or "disabled")
        reasoning_effort = tier_config.get("reasoning_effort") or alias_config.get("reasoning_effort")
        temperature = tier_config.get("temperature")
        max_tokens = tier_config.get("max_tokens")
        return RouteDecision(
            task_type=normalized_task,
            task_tier=tier,
            model_alias=alias,
            provider=provider,
            model=model,
            thinking_mode=thinking_mode,
            reasoning_effort=str(reasoning_effort) if reasoning_effort else None,
            temperature=float(temperature) if temperature is not None else None,
            max_tokens=int(max_tokens) if max_tokens is not None else None,
        )

    def validate(self) -> None:
        for task_type in self.task_types:
            self.resolve(task_type)
        for alias, config in self.aliases.items():
            model = str(config.get("model") or "")
            if model in self.deprecated_models:
                raise LLMProviderUnavailableError(f"Alias {alias} uses deprecated model: {model}")
