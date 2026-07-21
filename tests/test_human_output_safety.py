from __future__ import annotations

import pytest

from scripts.build_human_daily_output import _assert_safe_runtime


def test_human_output_allows_real_analysis_when_trading_is_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_TRADING", "false")
    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("LLM_GATEWAY_MOCK_ONLY", "false")

    _assert_safe_runtime()


def test_human_output_blocks_real_trading(monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_TRADING", "true")

    with pytest.raises(RuntimeError, match="UNSAFE_RUNTIME_SWITCHES"):
        _assert_safe_runtime()
