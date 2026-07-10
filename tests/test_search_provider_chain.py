from __future__ import annotations

from research.deepseek_unverified import DeepSeekUnverifiedResearchProvider
from research.schemas import ResearchQuery
from research.search_chain import SearchAttempt, SearchProviderChain, SearchProviderStatus


class Stub:
    def __init__(self, name, status):
        self.name, self.status = name, status
    def search(self, query):
        return SearchAttempt(provider=self.name, status=self.status)


def test_not_configured_quota_and_rate_limit_continue_to_unverified(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM_UNVERIFIED_RESEARCH_FALLBACK", "true")
    chain = SearchProviderChain([
        Stub("tavily", SearchProviderStatus.NOT_CONFIGURED),
        Stub("brave", SearchProviderStatus.QUOTA_EXHAUSTED),
        Stub("searxng", SearchProviderStatus.RATE_LIMITED),
    ], DeepSeekUnverifiedResearchProvider())
    result = chain.run(ResearchQuery(stock_code="000001.SZ", query="fundamental"), {"stock_code": "000001.SZ"})
    assert result.status is SearchProviderStatus.UNVERIFIED_FALLBACK
    assert result.evidence == []
    assert result.unverified_payload["display_marker"] == "*"
    assert result.unverified_payload["industry_chain"]["confidence"] <= 0.4
    assert result.unverified_payload["industry_position"]["confidence"] <= 0.3
    assert result.unverified_payload["domestic_substitution"]["confidence"] <= 0.25
    assert result.unverified_payload["current_market_main_theme"] == "UNKNOWN"
    assert result.unverified_payload["latest_industry_event"] == "UNKNOWN"


def test_all_fail_without_fallback_returns_unknown(monkeypatch):
    monkeypatch.setenv("ENABLE_LLM_UNVERIFIED_RESEARCH_FALLBACK", "false")
    result = SearchProviderChain([Stub("tavily", SearchProviderStatus.PROVIDER_ERROR)]).run(
        ResearchQuery(stock_code="000001.SZ", query="fundamental"), {}
    )
    assert result.status is SearchProviderStatus.UNKNOWN
    assert result.evidence == []
