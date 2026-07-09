from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from database.models.system import LLMUsage
from llm_gateway.cache import InMemoryLLMCache
from llm_gateway.exceptions import LLMProviderUnavailableError
from llm_gateway.registry import LLMProviderRegistry, get_llm_provider_registry
from llm_gateway.schemas import LLMRequest, LLMResponse


logger = logging.getLogger(__name__)


@dataclass
class LLMRouterConfig:
    mock_only: bool
    default_provider: str
    default_model: str
    cache_enabled: bool
    cache_ttl_seconds: int
    routing: dict[str, dict[str, str]]
    fallback: dict[str, str]
    budgets: dict[str, Any]


class LLMRouter:
    def __init__(
        self,
        config: LLMRouterConfig,
        registry: LLMProviderRegistry | None = None,
        cache: InMemoryLLMCache | None = None,
        db_session=None,
    ) -> None:
        self.config = config
        self.registry = registry or get_llm_provider_registry()
        self.cache = cache or InMemoryLLMCache()
        self.db_session = db_session

    def chat(self, request: LLMRequest) -> LLMResponse:
        provider_name, model = self._resolve_provider_and_model(request)
        routed_request = request.model_copy(update={"provider": provider_name, "model": model})
        request_hash = build_request_hash(routed_request)

        if self.config.cache_enabled:
            cached_response = self.cache.get(request_hash)
            if cached_response is not None:
                cached_response.cached = True
                self._record_usage(cached_response, routed_request, cached=True)
                return cached_response

        provider = self.registry.get_provider(provider_name)
        if not provider.enabled:
            if self.config.mock_only:
                provider = self.registry.get_provider("mock")
                routed_request = routed_request.model_copy(update={"provider": "mock", "model": model or self.config.default_model})
            else:
                provider = self._fallback_provider(provider_name)
                routed_request = routed_request.model_copy(update={"provider": provider.name, "model": self.config.default_model})

        response = provider.chat(routed_request)
        response.request_hash = request_hash
        response.cached = False
        if self.config.cache_enabled:
            self.cache.set(request_hash, response, self.config.cache_ttl_seconds)
        self._record_usage(response, routed_request, cached=False)
        return response

    def _resolve_provider_and_model(self, request: LLMRequest) -> tuple[str, str]:
        if self.config.mock_only:
            route = self.config.routing.get(request.agent_name) or self.config.routing.get("default", {})
            return "mock", route.get("model") or request.model or self.config.default_model

        route = self.config.routing.get(request.agent_name) or self.config.routing.get("default", {})
        provider = request.provider or route.get("provider") or self.config.default_provider
        model = request.model or route.get("model") or self.config.default_model
        return provider, model

    def _record_usage(self, response: LLMResponse, request: LLMRequest, cached: bool) -> None:
        if self.db_session is None:
            return
        try:
            self.db_session.add(
                LLMUsage(
                    provider=response.provider,
                    model_name=response.model,
                    agent_name=request.agent_name,
                    task=request.task,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cached_input_tokens=response.input_tokens if cached else 0,
                    total_tokens=response.total_tokens,
                    cost_usd=response.cost_usd,
                    latency_ms=response.latency_ms,
                    status=response.status,
                    error_message=None,
                    request_hash=response.request_hash,
                )
            )
            self.db_session.commit()
        except Exception as exc:
            logger.warning("Failed to record LLM usage: %s", exc)
            self.db_session.rollback()

    def _fallback_provider(self, requested_provider: str):
        candidates = [
            self.config.fallback.get("primary"),
            self.config.fallback.get("secondary"),
            self.config.fallback.get("final"),
            "mock",
        ]
        for candidate in candidates:
            if not candidate or candidate == requested_provider:
                continue
            try:
                provider = self.registry.get_provider(candidate)
            except Exception:
                continue
            if provider.enabled:
                return provider
        raise LLMProviderUnavailableError(f"Provider is disabled: {requested_provider}")


def build_request_hash(request: LLMRequest) -> str:
    payload = request.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
