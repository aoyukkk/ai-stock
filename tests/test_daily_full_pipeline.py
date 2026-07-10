from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from trader_demo.budget import (
    PipelineBudgetConfig,
    PipelineBudgetExceeded,
    PipelineBudgetManager,
    canary_projection,
)
from trader_demo.runtime import REAL_LLM_ENV, temporary_real_llm_runtime
from trader_demo.pro_aggregation import ProAggregationError, ProAggregationService
from trader_demo.service import select_model_validation_items
from llm_gateway.schemas import LLMResponse


def _item(code: str, rank: int, score: float, confidence: float, *, status: str = "SUCCESS"):
    return {
        "rank_row": SimpleNamespace(stock_code=code, rank=rank),
        "profile": SimpleNamespace(financial_status={"status": "STABLE"}),
        "screening": {
            "llm_score": score,
            "confidence": confidence,
            "_trader_demo": {"execution_status": status},
        },
    }


def test_five_million_budget_partition_and_reserve():
    config = PipelineBudgetConfig()
    config.validate()
    assert config.daily_limit == 5_000_000
    assert config.warning_threshold == 4_000_000
    assert config.regular_task_stop == 4_700_000
    assert config.flash_limit + config.pro_limit + config.repair_limit + config.final_reserve == 5_000_000

    manager = PipelineBudgetManager(config)
    manager.record("flash", input_tokens=3_500_000, output_tokens=0)
    manager.record("pro", input_tokens=800_000, output_tokens=0)
    manager.record("repair", input_tokens=399_999, output_tokens=0)
    with pytest.raises(PipelineBudgetExceeded, match="RESERVE"):
        manager.record("connectivity", input_tokens=1, output_tokens=0)


def test_canary_projection_uses_observed_percentiles():
    projection = canary_projection(
        [100, 120, 110, 130, 500], [20, 25, 22, 30, 100], total_task_count=210
    )
    assert projection["input_p50"] == 120
    assert projection["input_p90"] == 500
    assert projection["output_p50"] == 25
    assert projection["projected_total"] > projection["expected_tokens"]


def test_model_validation_top20_is_stable_and_excludes_failures():
    items = [_item(f"{index:06d}.SZ", index, 90 - index, 0.8) for index in range(1, 23)]
    items.append(_item("600000.SH", 100, 100, 1, status="FAILED"))
    first = select_model_validation_items(items, 20)
    second = select_model_validation_items(list(reversed(items)), 20)
    assert [item["rank_row"].stock_code for item in first] == [item["rank_row"].stock_code for item in second]
    assert len(first) == 20
    assert "600000.SH" not in {item["rank_row"].stock_code for item in first}


def test_real_llm_runtime_restores_switches(monkeypatch):
    original = {
        "LLM_REAL_CALLS_ENABLED": "false",
        "RUN_REAL_FUNDAMENTAL_RESEARCH": "false",
        "LLM_GATEWAY_MOCK_ONLY": "true",
    }
    for key, value in original.items():
        monkeypatch.setenv(key, value)
    with temporary_real_llm_runtime():
        assert {key: os.environ[key] for key in REAL_LLM_ENV} == REAL_LLM_ENV
    assert {key: os.environ[key] for key in original} == original


def test_pro_final_schema_failure_keeps_usage_for_audit():
    responses = [
        LLMResponse(
            provider="deepseek", model="deepseek-v4-pro", model_alias="controller-high-capability",
            content="{}", input_tokens=100, output_tokens=20, total_tokens=120,
            cost_usd=0.01, latency_ms=10, request_hash="first", status="ok",
        ),
        LLMResponse(
            provider="deepseek", model="deepseek-v4-pro", model_alias="controller-high-capability",
            content="{}", input_tokens=110, output_tokens=25, total_tokens=135,
            cost_usd=0.02, latency_ms=12, request_hash="repair", status="ok",
        ),
    ]

    class Gateway:
        def chat(self, _request):
            return responses.pop(0)

    sample = SimpleNamespace(
        stock_code="000001.SZ", stock_name="样本", rank=1,
        quant_scores={"total_score": 80}, fundamental_result={},
        screening_result={"llm_score": 70, "confidence": 0.8},
    )
    with pytest.raises(ProAggregationError) as caught:
        ProAggregationService(Gateway()).run([sample], selection_sources={"000001.SZ": "LLM_TOP20"})
    assert caught.value.usage.input_tokens == 210
    assert caught.value.usage.output_tokens == 45
    assert caught.value.audit["schema_status"] == "PRO_AGGREGATION_SCHEMA_FAILED"
    assert caught.value.audit["diagnostics"]["repair_attempted"] is True
