from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from fundamentals.concepts import TushareConceptIndex
from position_sizing.engine import PositionSizingEngine
from position_sizing.schemas import AccountState, SizingCandidate
from research.flash_v4 import (
    FLASH_SCORE_VERSION,
    FlashBatchDegenerateError,
    FlashScoringConfig,
    assert_flash_batch_quality,
    calculate_flash_v4,
)
from research.structured_validation import _flash_component_semantic_error
from research.wire_schemas import (
    FlashComponentWireV4,
    FundamentalEnrichmentWireV4,
    flash_component_v4_example,
    fundamental_v4_example,
    fundamental_v4_to_domain,
)


def _components(**updates):
    value = {
        "quant_consistency_score": 80,
        "fundamental_quality_score": 70,
        "financial_quality_score": 60,
        "risk_fit_score": 90,
        "data_quality_score": 75,
        "data_quality_penalty": 4,
        "risk_penalty": 0,
        "confidence": 0.72,
        "reason": "结构化输入存在区分度。",
        "risk_note": "仍需人工复核。",
        "data_conflict": False,
        "requires_manual_review": True,
        "evidence_fields": ["quant.total_score"],
        "missing_data": [],
    }
    value.update(updates)
    return value


def test_flash_v4_local_score_uses_configured_weights_and_100_point_scale():
    result = calculate_flash_v4("603019.SH", _components(), FlashScoringConfig())
    assert result["llm_score"] == Decimal("70.5")
    assert result["screening_decision"] == "ADVANCE"
    assert result["data_quality_score"] == Decimal("75")
    assert result["flash_score_version"] == FLASH_SCORE_VERSION
    json.dumps(result)


def test_flash_v4_data_quality_rejects_fractional_scale():
    with pytest.raises(ValueError, match="DATA_QUALITY_SCORE_SCALE_INVALID"):
        calculate_flash_v4("603019.SH", _components(data_quality_score=0.8), FlashScoringConfig())


def test_flash_v4_batch_rejects_degenerate_scores_decisions_and_quant_fallback():
    rows = [
        {"stock_code": f"0000{i:02d}.SZ", "llm_score": Decimal("0"), "screening_decision": "WATCH_ONLY", "confidence": Decimal("0.1")}
        for i in range(20)
    ]
    with pytest.raises(FlashBatchDegenerateError, match="FLASH_SCORE_DEGENERATE") as exc_info:
        assert_flash_batch_quality(rows)
    assert exc_info.value.audit["degenerate"] is True
    assert exc_info.value.audit["most_common_score_ratio"] == 1.0


def test_flash_v4_batch_accepts_meaningful_distribution():
    rows = [
        {
            "stock_code": f"0000{i:02d}.SZ",
            "llm_score": Decimal(str(35 + i * 2)),
            "screening_decision": "ADVANCE" if i > 16 else "HOLD" if i > 9 else "WATCH_ONLY",
            "confidence": Decimal(str(0.4 + i / 100)),
        }
        for i in range(20)
    ]
    audit = assert_flash_batch_quality(rows)
    assert audit["unique_score_count"] == 20
    assert audit["degenerate"] is False


def test_flash_v4_batch_accepts_varied_scores_in_one_decision_band():
    rows = [
        {
            "stock_code": f"0000{i:02d}.SZ",
            "llm_score": Decimal(str(55 + i / 2)),
            "screening_decision": "HOLD",
            "confidence": Decimal("0.70"),
        }
        for i in range(20)
    ]
    audit = assert_flash_batch_quality(rows)
    assert audit["unique_decision_count"] == 1
    assert audit["unique_score_count"] == 20
    assert audit["degenerate"] is False


def test_flash_v4_prompt_example_is_not_neutral_fifty_fallback():
    example = flash_component_v4_example("603019.SH")
    component_scores = {
        example[key]
        for key in (
            "quant_consistency_score", "fundamental_quality_score",
            "financial_quality_score", "risk_fit_score", "data_quality_score",
        )
    }
    assert len(component_scores) > 1
    assert example["stock_code"] == "603019.SH"


def test_flash_v4_rejects_copied_example_and_identical_component_scores():
    example = flash_component_v4_example("603019.SH")
    copied = FlashComponentWireV4.model_validate(example)
    assert _flash_component_semantic_error(copied, FlashComponentWireV4, example)[0] == (
        "DEGENERATE_COMPONENT_RESPONSE"
    )

    identical_payload = {**example}
    for key in (
        "quant_consistency_score", "fundamental_quality_score", "financial_quality_score",
        "risk_fit_score", "data_quality_score",
    ):
        identical_payload[key] = 50
    identical = FlashComponentWireV4.model_validate(identical_payload)
    assert _flash_component_semantic_error(identical, FlashComponentWireV4, example)[0] == (
        "DEGENERATE_COMPONENT_RESPONSE"
    )


def test_flash_v4_accepts_meaningfully_varied_components():
    example = flash_component_v4_example("603019.SH")
    payload = {**example, "quant_consistency_score": 81, "risk_fit_score": 72}
    parsed = FlashComponentWireV4.model_validate(payload)
    assert _flash_component_semantic_error(parsed, FlashComponentWireV4, example) == ("", "", "")


def test_tushare_concept_reverse_index_normalizes_suffix_and_audits_counts(tmp_path: Path):
    (tmp_path / "ths_index_all.json").write_text(json.dumps([
        {"ts_code": "885001.TI", "name": "人工智能"},
        {"ts_code": "885002.TI", "name": "算力"},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "ths_member_all.json").write_text(json.dumps([
        {"ts_code": "885001.TI", "con_code": "603019.SH"},
        {"ts_code": "885002.TI", "con_code": "603019"},
    ], ensure_ascii=False), encoding="utf-8")
    index = TushareConceptIndex(tmp_path)
    result = index.lookup("603019")
    assert result.normalized_tags == ["人工智能", "算力"]
    assert result.raw_source_count == 2
    assert result.source_status == "VERIFIED_STRUCTURED"


def test_fundamental_v4_enrichment_keeps_verified_tags_and_marks_inference():
    wire = FundamentalEnrichmentWireV4.model_validate({
        "schema_version": "fundamental_enrichment_wire_v4",
        "stock_code": "603019.SH",
        "core_products": ["高性能计算机"],
        "industry_chain_name": "算力基础设施产业链",
        "chain_position": "MIDSTREAM",
        "normalized_concept_tags": ["算力"],
        "inferred_concept_tags": ["高性能计算*"],
        "industry_position": "MAJOR_PARTICIPANT",
        "industry_position_description": "依据主营结构判断，缺少外部排名证据。",
        "structural_theme_fit": "算力基础设施需求的结构性增长可能带来机会。",
        "competitive_advantage": "可能具备系统集成能力，但仍需核验。",
        "structural_industry_trend": "计算基础设施持续升级。",
        "investment_logic": "主营与算力基础设施投入相关。",
        "invalidation_conditions": ["主营需求持续下降"],
        "domestic_substitution": "MODERATE",
        "observation_rating": "KEY_WATCH",
        "financial_status_explanation": "沿用规则财务状态。",
        "confidence": 0.4,
        "data_conflict": False,
        "evidence_fields": ["main_business"],
        "missing_fields": [],
    })
    context = {
        "stock_code": "603019.SH",
        "concept_tags": ["算力"],
        "concept_source_status": "VERIFIED_STRUCTURED",
        "financial_status": {"status": "NORMAL"},
        "main_business": [{"bz_item": "高性能计算机"}],
        "manifest": {"decision_time": "2026-07-10T15:30:00+08:00"},
    }
    result = fundamental_v4_to_domain(wire, context)
    assert result["industry_chain"]["chain_name"] != "UNKNOWN"
    assert result["concept_tags"][0]["source_status"] == "VERIFIED_STRUCTURED"
    assert result["concept_tags"][1]["source_status"] == "LLM_UNVERIFIED"
    assert result["concept_mapping_audit"] == {
        "inferred_concept_tag_count": 1,
        "final_concept_tag_count": 2,
        "concept_source_status": "MIXED_VERIFIED_AND_LLM_UNVERIFIED",
    }
    assert result["financial_status"] == {"status": "NORMAL"}


def test_fundamental_v4_audit_marks_inference_without_structured_tags():
    payload = fundamental_v4_example("603019.SH")
    payload["normalized_concept_tags"] = []
    payload["inferred_concept_tags"] = ["算力服务*"]
    wire = FundamentalEnrichmentWireV4.model_validate(payload)
    result = fundamental_v4_to_domain(wire, {
        "stock_code": "603019.SH", "concept_tags": [],
        "concept_source_status": "UNKNOWN", "financial_status": {"status": "NORMAL"},
    })
    assert result["concept_mapping_audit"]["inferred_concept_tag_count"] == 1
    assert result["concept_mapping_audit"]["concept_source_status"] == "LLM_UNVERIFIED"


@pytest.mark.parametrize(
    ("unrounded", "rounded", "expect_zero"),
    [
        (Decimal("92.000"), Decimal("92.00"), False),
        (Decimal("92.004"), Decimal("92.00"), False),
        (Decimal("91.98"), Decimal("91.98"), True),
    ],
)
def test_stop_validation_uses_unrounded_distance_and_one_tick_tolerance(unrounded, rounded, expect_zero):
    candidate = SizingCandidate(
        stock_code="000001.SZ", final_score=80, controller_confidence=Decimal("0.8"),
        entry_price=Decimal("100"), stop_price=rounded, unrounded_stop_price=unrounded,
        max_acceptable_price=Decimal("101"), risk_reward=Decimal("2"),
        average_daily_amount=Decimal("100000000"), industry="银行", industry_chain="金融服务",
        tick_size=Decimal("0.01"), stop_validation_tolerance_ticks=1,
    )
    result = PositionSizingEngine().evaluate(
        AccountState(equity=Decimal("1000000"), available_cash=Decimal("1000000")), [candidate]
    ).suggestions[0]
    assert (result.suggested_quantity == 0) is expect_zero
    if expect_zero:
        assert "UPSTREAM_STOP_LOSS_CONFLICT" in result.warnings


def test_model_validation_advisory_mode_can_compute_nonzero_quantity():
    candidate = SizingCandidate(
        stock_code="000001.SZ", final_score=85, controller_confidence=Decimal("0.8"),
        data_quality_factor=Decimal("0.9"), entry_price=Decimal("10"),
        stop_price=Decimal("9.5"), unrounded_stop_price=Decimal("9.5"),
        max_acceptable_price=Decimal("10.2"), risk_reward=Decimal("2"),
        average_daily_amount=Decimal("100000000"), industry="银行", industry_chain="金融服务",
    )
    suggestion = PositionSizingEngine().evaluate(
        AccountState(equity=Decimal("1000000"), available_cash=Decimal("1000000")), [candidate]
    ).suggestions[0]
    assert suggestion.suggested_quantity > 0
    assert suggestion.suggested_capital > 0
