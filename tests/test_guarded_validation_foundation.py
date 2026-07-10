from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from model_validation.service import _estimated_limits
from research.knowledge_mode import LLMKnowledgeMode, validate_knowledge_mode
from research.structured_validation import StructuredLightScreening, StructuredValidationProvider
from temporal.schemas import RunMode
from temporal.watermarks import DatasetWatermarkService


def test_historical_replay_requires_structured_input_only():
    validate_knowledge_mode(RunMode.HISTORICAL_REPLAY, LLMKnowledgeMode.STRUCTURED_INPUT_ONLY)
    with pytest.raises(ValueError, match="HISTORICAL_RUN_CANNOT_USE_UNBOUNDED_MODEL_KNOWLEDGE"):
        validate_knowledge_mode(RunMode.HISTORICAL_REPLAY, LLMKnowledgeMode.LLM_UNVERIFIED_CURRENT)


def test_structured_dry_screening_makes_no_gateway_call():
    class NeverGateway:
        def chat(self, _request):
            raise AssertionError("gateway must not be called")

    result = StructuredValidationProvider(NeverGateway()).screening(
        {"stock_code": "000001", "missing_fields": ["news"]},
        run_mode="HISTORICAL_REPLAY", use_real_llm=False,
    )
    assert result["screening_decision"] == "WATCH_ONLY"
    assert result["missing_data"] == ["news"]


def test_screening_conflict_cannot_advance():
    output = StructuredLightScreening(
        stock_code="000001", screening_decision="ADVANCE", llm_score=90, confidence=0.9,
        reason="structured", risk_note="conflict", data_conflict=True,
    )
    assert output.screening_decision == "HOLD"
    assert output.confidence <= 0.4


def test_limit_rule_is_board_aware_and_not_uniform_ten_percent():
    normal_up, _, _ = _estimated_limits("000001", "平安银行", {}, 10, date(2026, 7, 10))
    star_up, _, _ = _estimated_limits("688001", "科技公司", {}, 10, date(2026, 7, 10))
    st_up, _, _ = _estimated_limits("000002", "ST样本", {}, 10, date(2026, 7, 10))
    assert normal_up == 11
    assert star_up == 12
    assert st_up == 10.5


def test_watermark_exposes_expected_universe_audit_fields():
    watermark = DatasetWatermarkService().trade_date_watermark("daily_basic", date(2026, 7, 9))
    assert watermark.raw_actual_count >= watermark.unique_stock_count
    assert watermark.expected_stock_count == watermark.expected_count
    assert watermark.duplicate_count >= 0
    assert watermark.unexpected_stock_count >= 0
