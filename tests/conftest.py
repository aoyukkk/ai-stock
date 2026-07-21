from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_external_llm_calls(monkeypatch):
    """Unit and integration tests must opt in explicitly to any real LLM path."""
    monkeypatch.setenv("LLM_GATEWAY_MOCK_ONLY", "true")
    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "false")
    monkeypatch.setenv("RUN_REAL_FUNDAMENTAL_RESEARCH", "false")
