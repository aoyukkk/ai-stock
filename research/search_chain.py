from __future__ import annotations

import os
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from research.schemas import ResearchEvidence, ResearchQuery


class SearchProviderStatus(StrEnum):
    SUCCESS = "SUCCESS"
    CACHE_HIT = "CACHE_HIT"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    DISABLED = "DISABLED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NO_VALID_RESULT = "NO_VALID_RESULT"
    UNVERIFIED_FALLBACK = "UNVERIFIED_FALLBACK"
    FLASH_V4_DIRECT_SEARCH_FALLBACK = "FLASH_V4_DIRECT_SEARCH_FALLBACK"
    UNKNOWN = "UNKNOWN"


class SearchAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    status: SearchProviderStatus
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    message: str | None = None


class SearchChainResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SearchProviderStatus
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    attempts: list[SearchAttempt]
    degradation_level: str
    unverified_payload: dict[str, Any] | None = None


class SearchProvider(Protocol):
    name: str
    def search(self, query: ResearchQuery) -> SearchAttempt: ...


class ConfiguredSearchAdapter:
    """Formal search adapter contract; HTTP transport must be explicitly injected."""

    def __init__(
        self,
        name: str,
        key_env: str,
        enabled_env: str,
        transport=None,
    ) -> None:
        self.name = name
        self.key_env = key_env
        self.enabled_env = enabled_env
        self.transport = transport

    def search(self, query: ResearchQuery) -> SearchAttempt:
        if not _flag(self.enabled_env):
            return SearchAttempt(provider=self.name, status=SearchProviderStatus.DISABLED)
        if not os.getenv(self.key_env, "").strip():
            return SearchAttempt(provider=self.name, status=SearchProviderStatus.NOT_CONFIGURED)
        if not _flag("SEARCH_REAL_CALLS_ENABLED") or self.transport is None:
            return SearchAttempt(provider=self.name, status=SearchProviderStatus.DISABLED)
        try:
            evidence = list(self.transport(query))
        except TimeoutError:
            return SearchAttempt(provider=self.name, status=SearchProviderStatus.TIMEOUT)
        except Exception as exc:
            category = getattr(exc, "status", "")
            if category == 429:
                return SearchAttempt(provider=self.name, status=SearchProviderStatus.RATE_LIMITED)
            if category in {402, 403}:
                return SearchAttempt(provider=self.name, status=SearchProviderStatus.QUOTA_EXHAUSTED)
            return SearchAttempt(provider=self.name, status=SearchProviderStatus.PROVIDER_ERROR)
        valid = [item for item in evidence if item.url.startswith(("http://", "https://"))]
        return SearchAttempt(
            provider=self.name,
            status=SearchProviderStatus.SUCCESS if valid else SearchProviderStatus.NO_VALID_RESULT,
            evidence=valid,
        )


class SearchProviderChain:
    def __init__(self, providers: list[SearchProvider], unverified_fallback=None) -> None:
        self.providers = providers
        self.unverified_fallback = unverified_fallback

    def run(self, query: ResearchQuery, context: dict[str, Any], *, use_real_llm: bool = False, temporal_manifest=None) -> SearchChainResult:
        attempts = []
        for provider in self.providers:
            attempt = provider.search(query)
            attempts.append(attempt)
            if attempt.status in {SearchProviderStatus.SUCCESS, SearchProviderStatus.CACHE_HIT} and attempt.evidence:
                return SearchChainResult(
                    status=attempt.status,
                    evidence=attempt.evidence,
                    attempts=attempts,
                    degradation_level="WEB_VERIFIED",
                )
        if self.unverified_fallback is not None and _flag("ENABLE_LLM_UNVERIFIED_RESEARCH_FALLBACK", True):
            payload = self.unverified_fallback.infer(context, use_real_llm=use_real_llm, temporal_manifest=temporal_manifest)
            attempts.append(
                SearchAttempt(
                    provider="flash_v4_direct_search",
                    status=SearchProviderStatus.FLASH_V4_DIRECT_SEARCH_FALLBACK,
                    message="Allowed direct-search fallback; evidence confidence is degraded.",
                )
            )
            return SearchChainResult(
                status=SearchProviderStatus.UNVERIFIED_FALLBACK,
                attempts=attempts,
                degradation_level="FLASH_V4_DIRECT_SEARCH_FALLBACK",
                unverified_payload=payload,
            )
        return SearchChainResult(status=SearchProviderStatus.UNKNOWN, attempts=attempts, degradation_level="UNKNOWN")


def default_search_providers() -> list[ConfiguredSearchAdapter]:
    return [
        ConfiguredSearchAdapter("tavily", "TAVILY_API_KEY", "ENABLE_TAVILY_SEARCH"),
        ConfiguredSearchAdapter("brave", "BRAVE_SEARCH_API_KEY", "ENABLE_BRAVE_SEARCH"),
        ConfiguredSearchAdapter("searxng", "SEARXNG_BASE_URL", "ENABLE_SEARXNG_SEARCH"),
    ]


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
