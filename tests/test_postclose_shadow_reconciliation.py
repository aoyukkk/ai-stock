from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from post_close.shadow_integration import PostCloseShadowIntegrationService
from quant.explainability.factor_attribution import (
    OPAQUE_LLM_CONTRIBUTION,
    FactorAttributionEngine,
)
from quant.explainability.factor_registry import FACTOR_FAMILIES, FUNDAMENTAL
from quant.explainability.timing_contract import StrategyTimingContractSpec, validate_timing_contract
from scripts.export_postclose_shadow_reconciliation import _six, _v22_status


SHANGHAI = ZoneInfo("Asia/Shanghai")


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _Session:
    def __init__(self, count=7):
        self.count = count

    def scalar(self, _query):
        return self.count


def test_shadow_integration_preserves_baseline_and_orders(monkeypatch):
    monkeypatch.setattr(
        "post_close.shadow_integration.EntryTimingV2ShadowService.run",
        lambda *_args, **_kwargs: {"run_id": "v2", "shadow_only": True},
    )
    monkeypatch.setattr(
        "post_close.shadow_integration.DecisionExplainabilityShadowService.run",
        lambda *_args, **_kwargs: {"run_id": "v3", "shadow_only": True},
    )
    baseline = {"candidates": ["002832.SZ"], "positions": {"002832.SZ": 0.9}}
    result = PostCloseShadowIntegrationService(_Session()).run(
        trade_date=date(2026, 7, 20),
        quant_run_id="quant",
        available_at_ts=datetime(2026, 7, 20, 17, 0, tzinfo=SHANGHAI),
        baseline_snapshot=baseline,
    )
    assert result["baseline_unchanged"] is True
    assert result["orders_created"] == 0
    assert result["formal_permission"] is False
    assert baseline["candidates"] == ["002832.SZ"]


def test_shadow_integration_fails_if_order_plan_changes(monkeypatch):
    service = PostCloseShadowIntegrationService(_Session())
    counts = iter([7, 8])
    monkeypatch.setattr(service, "_order_count", lambda: next(counts))
    monkeypatch.setattr(
        "post_close.shadow_integration.EntryTimingV2ShadowService.run",
        lambda *_args, **_kwargs: {"run_id": "v2"},
    )
    monkeypatch.setattr(
        "post_close.shadow_integration.DecisionExplainabilityShadowService.run",
        lambda *_args, **_kwargs: {"run_id": "v3"},
    )
    with pytest.raises(RuntimeError, match="SHADOW_CREATED_ORDER_PLAN"):
        service.run(
            trade_date=date(2026, 7, 20),
            quant_run_id="quant",
            available_at_ts=datetime(2026, 7, 20, 17, 0, tzinfo=SHANGHAI),
            baseline_snapshot={"candidates": []},
        )


def test_factor_attribution_has_six_families_and_explicit_opaque_llm_marker():
    rows = FactorAttributionEngine().attribute(
        stock_code="002832.SZ",
        trade_date=date(2026, 7, 20),
        technical_score=60,
        capital_score=61,
        emotion_score=62,
        momentum_score=63,
        risk_score=64,
        flash_score=65,
    )
    assert {row.factor_family for row in rows} == set(FACTOR_FAMILIES)
    fundamental = next(row for row in rows if row.factor_family == FUNDAMENTAL)
    assert fundamental.raw_signal["contribution_marker"] == OPAQUE_LLM_CONTRIBUTION
    assert OPAQUE_LLM_CONTRIBUTION in fundamental.interaction_note
    assert fundamental.score_contribution == 0


def test_actual_postclose_timing_contract_requires_next_eligible_open():
    spec = StrategyTimingContractSpec(
        stock_code="002832.SZ",
        trade_date=date(2026, 7, 20),
        observation_end_ts=datetime(2026, 7, 20, 15, 0, tzinfo=SHANGHAI),
        available_at_ts=datetime(2026, 7, 20, 17, 0, tzinfo=SHANGHAI),
        signal_generated_at=datetime(2026, 7, 21, 13, 51, tzinfo=SHANGHAI),
        order_eligible_at=datetime(2026, 7, 22, 9, 30, tzinfo=SHANGHAI),
        execution_policy="NEXT_OPEN_SHADOW_ONLY",
        feature_version="test",
        data_snapshot_id="manifest",
        universe_snapshot_id="universe",
    )
    validate_timing_contract(spec)


def test_export_semantics_preserve_six_digit_codes_and_shadow_statuses():
    assert _six(2832) == "002832"
    assert _six("000001.SZ") == "000001"
    row = SimpleNamespace(admission_status_v2="BLOCK", data_coverage_json={})
    assert _v22_status(row) == "SHADOW_BLOCK"
    insufficient = SimpleNamespace(admission_status_v2="BLOCK", data_coverage_json={"status": "DATA_INSUFFICIENT"})
    assert _v22_status(insufficient) == "SHADOW_DATA_INSUFFICIENT"
