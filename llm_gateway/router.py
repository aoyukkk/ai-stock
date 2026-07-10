from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from backend.core.security import is_sensitive_key, redact_sensitive_text
from database.models.system import LLMUsage
from llm_gateway.cache import InMemoryLLMCache
from llm_gateway.exceptions import (
    LLMAuthenticationError,
    LLMGatewayError,
    LLMInsufficientBalanceError,
    LLMModelNotAvailableError,
    LLMProviderNotConfiguredError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMSchemaValidationError,
    LLMTimeoutError,
)
from llm_gateway.json_output import parse_json_object, validate_json_schema
from llm_gateway.pricing import apply_configured_cost
from llm_gateway.registry import LLMProviderRegistry, get_llm_provider_registry
from llm_gateway.routing_policy import ModelRoutingPolicy, RouteDecision, TaskTier
from llm_gateway.schemas import LLMMessage, LLMRequest, LLMResponse


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
    aliases: dict[str, dict[str, Any]] = field(default_factory=dict)
    task_types: dict[str, str] = field(default_factory=dict)
    tier_routing: dict[str, dict[str, Any]] = field(default_factory=dict)
    pricing: dict[str, Any] = field(default_factory=dict)


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
        self.policy = ModelRoutingPolicy(
            aliases=config.aliases,
            task_types=config.task_types,
            tiers=config.tier_routing,
        ) if config.task_types and config.tier_routing else None
        if self.policy is not None:
            self.policy.validate()

    def preview_route(self, task_type: str, model_alias: str | None = None) -> dict[str, Any]:
        return self._target_decision(task_type, model_alias).as_dict()

    def check_model_availability(self, task_type: str, model_alias: str | None = None) -> dict[str, Any]:
        decision = self._target_decision(task_type, model_alias)
        provider = self.registry.get_provider(decision.provider)
        available = provider.fetch_available_models()
        if decision.model not in available:
            raise LLMModelNotAvailableError(f"Configured model is not available: {decision.model_alias}")
        return {**decision.as_dict(), "available": True, "available_model_count": len(available)}

    def chat(self, request: LLMRequest) -> LLMResponse:
        try:
            routed_request = self._resolve_request(request)
        except LLMGatewayError as exc:
            request_hash = build_request_hash(request)
            response = self._error_response(request, request_hash, exc)
            self._record_usage(response, request, cached=False)
            return response
        request_hash = build_request_hash(routed_request)

        if routed_request.metadata.get("budget_exhausted"):
            response = self._error_response(
                routed_request,
                request_hash,
                RuntimeError("Configured LLM budget is exhausted."),
                status="budget_exceeded",
            )
            self._record_usage(response, routed_request, cached=False)
            return response

        if self.config.cache_enabled:
            cached_response = self.cache.get(request_hash)
            if cached_response is not None:
                cached_response.cached = True
                self._record_usage(cached_response, routed_request, cached=True)
                return cached_response

        response = self._call_with_fallback(routed_request, request_hash)
        response.request_hash = request_hash
        response.cached = False
        response = apply_configured_cost(response, self.config.pricing)
        if self.config.cache_enabled and response.status == "ok":
            self.cache.set(request_hash, response, self.config.cache_ttl_seconds)
        self._record_usage(response, routed_request, cached=False)
        return response

    def _resolve_request(self, request: LLMRequest) -> LLMRequest:
        task_type = request.task_type or request.task
        decision = self._target_decision(task_type, request.model_alias)
        explicit_real = bool(request.metadata.get("explicit_real_llm_test") or request.metadata.get("explicit_real_screening"))

        if self.config.mock_only and not explicit_real:
            mock_alias = "mock-reasoning" if decision.task_tier in {TaskTier.COMPLEX, TaskTier.HIGH_IMPACT} else "mock-fast"
            mapped = self.config.aliases.get(mock_alias, {"provider": "mock", "model": self.config.default_model})
            decision = RouteDecision(
                task_type=decision.task_type,
                task_tier=decision.task_tier,
                model_alias=mock_alias,
                provider="mock",
                model=str(mapped.get("model") or self.config.default_model),
                thinking_mode="disabled",
                reasoning_effort=None,
                temperature=decision.temperature,
                max_tokens=decision.max_tokens,
            )

        allow_fallback = request.allow_fallback
        if explicit_real and decision.task_tier in {TaskTier.COMPLEX, TaskTier.HIGH_IMPACT}:
            allow_fallback = False

        return request.model_copy(
            update={
                "task_type": decision.task_type,
                "task_tier": decision.task_tier.value,
                "model_alias": decision.model_alias,
                "provider": decision.provider,
                "model": decision.model,
                "thinking_mode": request.thinking_mode or decision.thinking_mode,
                "reasoning_effort": request.reasoning_effort or decision.reasoning_effort,
                "temperature": request.temperature if request.temperature is not None else decision.temperature,
                "max_tokens": request.max_tokens if request.max_tokens is not None else decision.max_tokens,
                "allow_fallback": allow_fallback,
            }
        )

    def _target_decision(self, task_type: str, model_alias: str | None) -> RouteDecision:
        if self.policy is not None:
            return self.policy.resolve(task_type, model_alias)

        route = self.config.routing.get(task_type) or self.config.routing.get("default", {})
        alias = model_alias or route.get("model_alias")
        if model_alias and model_alias not in self.config.aliases:
            raise LLMProviderUnavailableError(f"Unknown model alias: {model_alias}")
        if alias and alias in self.config.aliases:
            mapped = self.config.aliases[alias]
            return RouteDecision(
                task_type=task_type,
                task_tier=TaskTier.SIMPLE,
                model_alias=alias,
                provider=str(mapped["provider"]),
                model=str(mapped["model"]),
                thinking_mode="disabled",
                reasoning_effort=None,
                temperature=None,
                max_tokens=None,
            )
        provider = str(route.get("provider") or self.config.default_provider)
        model = str(route.get("model") or self.config.default_model)
        return RouteDecision(
            task_type=task_type,
            task_tier=TaskTier.SIMPLE,
            model_alias=alias or model,
            provider=provider,
            model=model,
            thinking_mode="disabled",
            reasoning_effort=None,
            temperature=None,
            max_tokens=None,
        )

    def _call_with_fallback(self, request: LLMRequest, request_hash: str) -> LLMResponse:
        candidates = [request.provider or self.config.default_provider]
        if request.allow_fallback:
            candidates.extend(
                value for value in (
                    self.config.fallback.get("primary"),
                    self.config.fallback.get("secondary"),
                    self.config.fallback.get("final"),
                    "mock",
                ) if value
            )

        seen: set[str] = set()
        last_error: Exception | None = None
        for provider_name in candidates:
            if provider_name in seen:
                continue
            seen.add(provider_name)
            try:
                provider = self.registry.get_provider(provider_name)
                if not provider.enabled:
                    raise LLMProviderUnavailableError(f"Provider is disabled: {provider_name}")
                if provider_name == request.provider:
                    provider_request = request
                else:
                    fallback_alias, fallback_model = self._fallback_alias_and_model(provider_name, request.task_tier)
                    provider_request = request.model_copy(
                        update={
                            "provider": provider_name,
                            "model": fallback_model,
                            "model_alias": fallback_alias,
                            "thinking_mode": "disabled",
                            "reasoning_effort": None,
                        }
                    )
                response = provider.chat(provider_request)
                response.model_alias = provider_request.model_alias
                response.task_type = provider_request.task_type
                response.task_tier = provider_request.task_tier
                response.thinking_mode = provider_request.thinking_mode
                response.reasoning_effort = provider_request.reasoning_effort
                return self._validate_or_repair(provider, provider_request, response)
            except (LLMGatewayError, ValueError) as exc:
                last_error = exc
                if not request.allow_fallback:
                    break
                continue

        return self._error_response(request, request_hash, last_error)

    def _validate_or_repair(self, provider, request: LLMRequest, response: LLMResponse) -> LLMResponse:
        if request.response_schema is None:
            if response.parsed_json is None and request.json_mode:
                response.parsed_json = parse_json_object(response.content)
                response.structured_output = response.parsed_json
            return response

        try:
            parsed = response.parsed_json or parse_json_object(response.content)
            validate_json_schema(parsed, request.response_schema)
            response.parsed_json = parsed
            response.structured_output = parsed
            return response
        except LLMSchemaValidationError as first_error:
            repair_request = request.model_copy(
                update={
                    "messages": request.messages + [
                        LLMMessage(role="assistant", content=response.content),
                        LLMMessage(
                            role="user",
                            content=(
                                "Repair the previous output. Return JSON only and match this JSON Schema exactly: "
                                + json.dumps(request.response_schema, ensure_ascii=False, separators=(",", ":"))
                            ),
                        ),
                    ],
                    "allow_fallback": False,
                    "json_mode": True,
                }
            )
            repaired = provider.chat(repair_request)
            try:
                parsed = repaired.parsed_json or parse_json_object(repaired.content)
                validate_json_schema(parsed, request.response_schema)
            except LLMSchemaValidationError as second_error:
                raise LLMSchemaValidationError(
                    f"Structured output failed validation after one repair: {second_error}"
                ) from first_error
            repaired.parsed_json = parsed
            repaired.structured_output = parsed
            repaired.input_tokens += response.input_tokens
            repaired.input_cache_hit_tokens += response.input_cache_hit_tokens
            repaired.input_cache_miss_tokens += response.input_cache_miss_tokens
            repaired.output_tokens += response.output_tokens
            repaired.total_tokens += response.total_tokens
            repaired.latency_ms += response.latency_ms
            repaired.model_alias = request.model_alias
            repaired.task_type = request.task_type
            repaired.task_tier = request.task_tier
            repaired.thinking_mode = request.thinking_mode
            repaired.reasoning_effort = request.reasoning_effort
            repaired.raw_response_metadata["repair_attempted"] = True
            return repaired

    def _fallback_alias_and_model(self, provider_name: str, task_tier: str | None) -> tuple[str, str]:
        preferred = "mock-reasoning" if task_tier in {TaskTier.COMPLEX.value, TaskTier.HIGH_IMPACT.value} else "mock-fast"
        mapped = self.config.aliases.get(preferred, {})
        if mapped.get("provider") == provider_name:
            return preferred, str(mapped.get("model"))
        for alias, value in self.config.aliases.items():
            if value.get("provider") == provider_name:
                return alias, str(value.get("model"))
        return provider_name, self.config.default_model

    def _record_usage(self, response: LLMResponse, request: LLMRequest, cached: bool) -> None:
        if self.db_session is None:
            return
        try:
            row = LLMUsage(
                provider=response.provider,
                model_name=response.model,
                model_alias=response.model_alias or request.model_alias,
                agent_name=request.agent_name,
                task=request.task,
                task_type=response.task_type or request.task_type or request.task,
                task_tier=response.task_tier or request.task_tier,
                thinking_mode=response.thinking_mode or request.thinking_mode,
                reasoning_effort=response.reasoning_effort or request.reasoning_effort,
                prompt_version=request.prompt_version,
                input_tokens=response.input_tokens,
                input_cache_hit_tokens=response.input_cache_hit_tokens,
                input_cache_miss_tokens=response.input_cache_miss_tokens,
                output_tokens=response.output_tokens,
                cached_input_tokens=response.input_tokens if cached else 0,
                total_tokens=response.total_tokens,
                cost_usd=response.cost_usd,
                cost_status=response.cost_status,
                pricing_version=response.pricing_version,
                latency_ms=response.latency_ms,
                status=response.status,
                error_message=redact_sensitive_text(response.error) if response.error else None,
                request_hash=response.request_hash,
            )
            self.db_session.add(row)
            self.db_session.commit()
            response.usage_id = row.id
        except Exception as exc:
            logger.warning("Failed to record LLM usage: %s", redact_sensitive_text(exc))
            self.db_session.rollback()

    def _error_response(
        self,
        request: LLMRequest,
        request_hash: str,
        error: Exception | None,
        status: str | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            provider=request.provider or "unknown",
            model=request.model or "unknown",
            model_alias=request.model_alias,
            task_type=request.task_type or request.task,
            task_tier=request.task_tier,
            thinking_mode=request.thinking_mode,
            reasoning_effort=request.reasoning_effort,
            content="",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=0,
            request_hash=request_hash,
            prompt_version=request.prompt_version,
            status=status or _status_for_error(error),
            error=redact_sensitive_text(error or "Provider request failed."),
        )


def _status_for_error(error: Exception | None) -> str:
    if isinstance(error, LLMProviderNotConfiguredError):
        return "not_configured"
    if isinstance(error, LLMAuthenticationError):
        return "auth_failed"
    if isinstance(error, LLMInsufficientBalanceError):
        return "insufficient_balance"
    if isinstance(error, LLMTimeoutError):
        return "timeout"
    if isinstance(error, LLMRateLimitError):
        return "rate_limited"
    if isinstance(error, LLMSchemaValidationError):
        return "schema_error"
    if isinstance(error, LLMModelNotAvailableError):
        return "model_not_available"
    return "provider_error"


def build_request_hash(request: LLMRequest) -> str:
    payload = {
        "agent": request.agent_name,
        "task": request.task,
        "task_type": request.task_type,
        "task_tier": request.task_tier,
        "model_alias": request.model_alias,
        "provider": request.provider,
        "model": request.model,
        "thinking_mode": request.thinking_mode,
        "reasoning_effort": request.reasoning_effort,
        "prompt_version": request.prompt_version,
        "messages": [message.model_dump(mode="json") for message in request.messages],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "response_schema": request.response_schema,
        "metadata": _safe_hash_metadata(request.metadata),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_hash_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_hash_metadata(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [_safe_hash_metadata(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
