from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import logging
from typing import Any

from backend.core.config import get_app_config
from backend.core.config_manager import ConfigManager
from backend.core.security import sanitize_config
from llm_gateway.cache import InMemoryLLMCache
from llm_gateway.prompt_manager import PromptManager
from llm_gateway.registry import LLMProviderRegistry, get_llm_provider_registry
from llm_gateway.router import LLMRouter, LLMRouterConfig
from llm_gateway.schemas import LLMMessage, LLMRequest, LLMResponse


@dataclass
class UsageSummary:
    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cached_count: int = 0
    budget_warning: bool = False
    budget_exhausted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_count": self.call_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "cached_count": self.cached_count,
            "budget_warning": self.budget_warning,
            "budget_exhausted": self.budget_exhausted,
        }


class LLMGatewayService:
    def __init__(
        self,
        registry: LLMProviderRegistry | None = None,
        prompt_manager: PromptManager | None = None,
        cache: InMemoryLLMCache | None = None,
        db_session=None,
    ) -> None:
        self.config = load_llm_router_config()
        self.registry = registry or get_llm_provider_registry()
        self.prompt_manager = prompt_manager or PromptManager()
        self.cache = cache or InMemoryLLMCache()
        self.router = LLMRouter(
            config=self.config,
            registry=self.registry,
            cache=self.cache,
            db_session=db_session,
        )
        self.usage = UsageSummary()

    def chat(self, request: LLMRequest) -> LLMResponse:
        request = self._apply_budget_policy(request)
        response = self.router.chat(request)
        self._update_usage(response)
        return response

    def chat_simple(
        self,
        agent_name: str,
        task: str,
        user_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        prompt_version = None
        try:
            prompt = self.prompt_manager.get_active_prompt(agent_name)
            system_content = prompt.template
            prompt_version = prompt.version
        except Exception:
            system_content = "You are a mock LLM endpoint. Do not provide real trading instructions."

        request = LLMRequest(
            agent_name=agent_name,
            task=task,
            messages=[
                LLMMessage(role="system", content=system_content),
                LLMMessage(role="user", content=user_content),
            ],
            prompt_version=prompt_version,
            metadata=metadata or {},
        )
        return self.chat(request)

    def list_providers(self) -> list[dict[str, Any]]:
        return sanitize_config(self.registry.list_provider_info())

    def preview_route(self, task_type: str, model_alias: str | None = None) -> dict[str, Any]:
        return self.router.preview_route(task_type, model_alias)

    def check_model_availability(self, task_type: str, model_alias: str | None = None) -> dict[str, Any]:
        return self.router.check_model_availability(task_type, model_alias)

    def get_usage_summary(self) -> dict[str, Any]:
        return self.usage.as_dict()

    def status_summary(self) -> dict[str, Any]:
        return sanitize_config(
            {
                "mock_only": self.config.mock_only,
                "providers": self.list_providers(),
                "default_provider": self.config.default_provider,
                "routing_summary": self.config.routing,
                "cache_enabled": self.config.cache_enabled,
                "budgets": self.config.budgets,
            }
        )

    def _update_usage(self, response: LLMResponse) -> None:
        self.usage.call_count += 1
        self.usage.input_tokens += response.input_tokens
        self.usage.output_tokens += response.output_tokens
        self.usage.total_tokens += response.total_tokens
        self.usage.cost_usd += response.cost_usd or 0.0
        if response.cached:
            self.usage.cached_count += 1

    def _apply_budget_policy(self, request: LLMRequest) -> LLMRequest:
        budget = int(self.config.budgets.get("daily_token_budget") or 0)
        cost_budget = float(self.config.budgets.get("daily_cost_budget_usd") or 0)
        warning_percent = float(self.config.budgets.get("warning_percent") or 80)
        limit_percent = float(self.config.budgets.get("limit_percent") or 100)
        ratios = []
        if budget > 0:
            ratios.append(self.usage.total_tokens / budget * 100)
        if cost_budget > 0 and self.usage.cost_usd > 0:
            ratios.append(self.usage.cost_usd / cost_budget * 100)
        if not ratios:
            return request
        ratio = max(ratios)
        if ratio >= warning_percent:
            self.usage.budget_warning = True
            logging.getLogger(__name__).warning("LLM daily token budget reached %.1f%%.", ratio)
        if ratio < limit_percent:
            return request
        self.usage.budget_exhausted = True
        if not request.allow_fallback:
            return request.model_copy(update={"metadata": {**request.metadata, "budget_exhausted": True}})
        return request.model_copy(
            update={
                "model_alias": "mock-fast",
                "provider": None,
                "model": None,
                "metadata": {**request.metadata, "budget_fallback": True},
            }
        )


@lru_cache(maxsize=1)
def get_llm_gateway_service() -> LLMGatewayService:
    return LLMGatewayService()


def load_llm_router_config() -> LLMRouterConfig:
    models_config = ConfigManager().get_llm_gateway_config()
    gateway_config = models_config.get("llm_gateway", {})
    llm_config = models_config.get("llm", {})
    agent_routes = models_config.get("agent_routes", {})
    routing = _load_routing(llm_config, agent_routes)
    cache_config = llm_config.get("cache", {})
    budget_config = llm_config.get("budget", {})
    routing_policy = llm_config.get("routing_policy", {})
    pricing = get_app_config().config_files.get("token_cost", {}).get("pricing", {})

    mock_only = bool(llm_config.get("mock_only", False)) or gateway_config.get("phase0_mode") == "mock_only"
    ttl_minutes = int(llm_config.get("cache_ttl_minutes") or cache_config.get("ttl_minutes") or 30)
    return LLMRouterConfig(
        mock_only=mock_only,
        default_provider=str(llm_config.get("default_provider") or "mock"),
        default_model=str(llm_config.get("default_model") or "mock-chat"),
        cache_enabled=bool(llm_config.get("enable_cache", cache_config.get("enabled", True))),
        cache_ttl_seconds=ttl_minutes * 60,
        routing=routing,
        aliases={
            str(alias): dict(value)
            for alias, value in llm_config.get("aliases", {}).items()
            if isinstance(value, dict) and value.get("provider") and value.get("model")
        },
        fallback=llm_config.get("fallback", {"primary": "mock", "secondary": "mock", "final": "mock"}),
        budgets={
            "daily_token_budget": budget_config.get("daily_token_budget", 5000000),
            "warning_percent": budget_config.get("warning_percent", 80),
            "limit_percent": budget_config.get("limit_percent", 100),
            "daily_cost_budget_usd": budget_config.get("daily_cost_budget_usd", 8),
        },
        task_types=routing_policy.get("task_types", {}),
        tier_routing=routing_policy.get("tiers", {}),
        pricing=pricing,
    )


def _load_routing(llm_config: dict[str, Any], agent_routes: dict[str, Any]) -> dict[str, dict[str, str]]:
    routing = llm_config.get("routing")
    if isinstance(routing, dict) and routing:
        return {
            name: {
                "provider": str(route.get("provider", "mock")),
                "model": str(route.get("model", "mock-chat")),
                **({"model_alias": str(route["model_alias"])} if route.get("model_alias") else {}),
            }
            for name, route in routing.items()
            if isinstance(route, dict)
        }

    converted = {"default": {"model_alias": "mock-fast", "provider": "mock", "model": "mock-chat"}}
    for agent_name, route in agent_routes.items():
        if isinstance(route, dict):
            converted[agent_name] = {
                "provider": "mock",
                "model": str(route.get("phase0_model_alias") or "mock-chat"),
                "model_alias": str(route.get("phase0_model_alias") or "mock-fast"),
            }
    return converted
