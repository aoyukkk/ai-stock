from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fundamentals.profile import TushareFundamentalProfileBuilder
from llm_gateway.schemas import LLMResponse
from research.output_boundary import scan_output_boundary
from research.input_quality import summarize_structured_input
from research.structured_validation import (
    StructuredLightScreening,
    StructuredValidationProvider,
    _bounded_list_compactions,
)
from research.wire_schemas import (
    FundamentalEnrichmentWireV4,
    FundamentalInferenceWireV3,
    fundamental_v4_example,
    fundamental_wire_example,
    fundamental_wire_to_domain,
)
from stock_codes import display_stock_code, normalize_ts_code


def _response(content: str, *, finish_reason: str = "stop", status: str = "ok") -> LLMResponse:
    return LLMResponse(
        provider="deepseek", model="deepseek-v4-flash", model_alias="light-screening-default",
        content=content, input_tokens=10, output_tokens=5, total_tokens=15,
        latency_ms=7, request_hash="fixture", status=status, finish_reason=finish_reason,
        raw_response_metadata={"http_status": 200, "response_id": "fixture-id"},
    )


class QueueGateway:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.requests = []
        self.config = SimpleNamespace(mock_only=False)
        self.usage = SimpleNamespace(budget_exhausted=False)

    def chat(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def test_fundamental_wire_schema_is_flat_and_has_no_nullable_union():
    schema = FundamentalInferenceWireV3.model_json_schema()
    text = json.dumps(schema)
    assert "$ref" not in text
    assert "$defs" not in text
    assert "anyOf" not in text
    assert FundamentalInferenceWireV3.model_validate(fundamental_wire_example("688130.SH")).stock_code == "688130.SH"


def test_boundary_scanner_allows_negative_and_missing_statements():
    safe = {
        "stock_code": "688130.SH",
        "summary": "输入不足，无法确认该公司目前是否行业第一，也没有最新消息。",
    }
    assert scan_output_boundary(safe, expected_stock_code="688130.SH") is None
    assert scan_output_boundary(
        {"stock_code": "688130.SH", "limitations": "未提供市场份额和客户订单证据", "missing_fields": ["customer", "market_share"]},
        expected_stock_code="688130.SH",
    ) is None
    assert scan_output_boundary(
        {"stock_code": "688130.SH", "risk_note": "没有证据证明是行业龙头"},
        expected_stock_code="688130.SH",
    ) is None


def test_boundary_scanner_returns_precise_path_and_category():
    violation = scan_output_boundary(
        {"stock_code": "688130.SH", "items": [{"claim": "公司是国内第一龙头"}]},
        expected_stock_code="688130.SH",
    )
    assert violation is not None
    assert violation.category == "UNSUPPORTED_CLAIM"
    assert violation.path == "$.items[0].claim"
    assert scan_output_boundary(
        {"stock_code": "688130.SH", "url": "https://fiction.invalid/a"}, expected_stock_code="688130.SH"
    ).category == "FABRICATED_URL"


def test_empty_response_is_repaired_once_and_diagnostics_do_not_store_content(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("RUN_REAL_FUNDAMENTAL_RESEARCH", "true")
    repaired = {
        "schema_version": "flash_component_wire_v4", "stock_code": "001390.SZ",
        "quant_consistency_score": 52, "fundamental_quality_score": 49,
        "financial_quality_score": 47, "risk_fit_score": 51, "data_quality_score": 53,
        "data_quality_penalty": 0, "risk_penalty": 0,
        "confidence": 0.2, "reason": "输入有限", "risk_note": "需人工复核",
        "data_conflict": False, "requires_manual_review": True,
        "evidence_fields": [], "missing_data": [],
    }
    gateway = QueueGateway([_response(""), _response(json.dumps(repaired, ensure_ascii=False))])
    provider = StructuredValidationProvider(gateway)
    result = provider.screening(
        {"stock_code": "001390", "missing_fields": []},
        run_mode="HISTORICAL_REPLAY", use_real_llm=True,
    )
    assert result["screening_decision"] == "WATCH_ONLY"
    assert len(gateway.requests) == 2
    assert gateway.requests[0].response_schema is None
    assert gateway.requests[0].metadata["structured"] is True
    assert gateway.requests[0].max_tokens == 1400
    diagnostics = provider.audit[0]["diagnostics"]
    assert diagnostics["content_empty"] is True
    assert diagnostics["repair_attempted"] is True
    assert diagnostics["repair_category"] == "EMPTY_JSON_CONTENT"
    assert "content" not in diagnostics


def test_copied_flash_example_is_repaired_with_context(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("RUN_REAL_FUNDAMENTAL_RESEARCH", "true")
    from research.wire_schemas import flash_component_v4_example

    copied = flash_component_v4_example("001390.SZ")
    repaired = {
        **copied,
        "quant_consistency_score": 78,
        "fundamental_quality_score": 61,
        "financial_quality_score": 55,
        "risk_fit_score": 69,
        "data_quality_score": 83,
    }
    gateway = QueueGateway([
        _response(json.dumps(copied, ensure_ascii=False)),
        _response(json.dumps(repaired, ensure_ascii=False)),
    ])
    provider = StructuredValidationProvider(gateway)
    context = {"stock_code": "001390", "missing_fields": [], "quant": {"total_score": 78}}
    result = provider.screening(context, run_mode="HISTORICAL_REPLAY", use_real_llm=True)

    assert result["llm_score"] != 50
    assert len(gateway.requests) == 2
    repair_payload = json.loads(gateway.requests[1].messages[1].content)
    assert repair_payload["context"]["quant"]["total_score"] == 78
    assert "不得与example中的示例分数组合相同" in repair_payload["instruction"]
    assert provider.audit[0]["diagnostics"]["repair_category"] == "DEGENERATE_COMPONENT_RESPONSE"


def test_bounded_lists_are_deduplicated_and_compacted_before_schema_validation():
    payload = fundamental_v4_example("001390.SZ")
    payload["inferred_concept_tags"] = [
        "概念一", "概念一", "概念二", "概念三", "概念四", "概念五", "概念六", "概念七",
    ]
    response = _response(json.dumps(payload, ensure_ascii=False))

    category, _, _, parsed = StructuredValidationProvider._validate_response(
        response, FundamentalEnrichmentWireV4, "001390.SZ",
    )

    assert category == ""
    assert parsed is not None
    assert parsed.inferred_concept_tags == [
        "概念一", "概念二", "概念三", "概念四", "概念五", "概念六",
    ]
    assert _bounded_list_compactions(response, FundamentalEnrichmentWireV4) == [{
        "field": "inferred_concept_tags",
        "raw_count": 8,
        "unique_count": 7,
        "saved_count": 6,
    }]


def test_length_and_fence_have_distinct_categories():
    category, _, _, _ = StructuredValidationProvider._validate_response(
        _response('{"stock_code":"001390.SZ"', finish_reason="length"),
        StructuredLightScreening, "001390.SZ",
    )
    assert category == "JSON_TRUNCATED"
    category, _, _, _ = StructuredValidationProvider._validate_response(
        _response("```json\n{}\n```"), StructuredLightScreening, "001390.SZ",
    )
    assert category == "JSON_FENCE_VIOLATION"
    category, _, _, _ = StructuredValidationProvider._validate_response(
        _response('{"stock_code":"001390.SZ"} trailing'), StructuredLightScreening, "001390.SZ",
    )
    assert category == "JSON_TRAILING_CONTENT"
    category, _, _, _ = StructuredValidationProvider._validate_response(
        _response('{"a":1}{"b":2}'), StructuredLightScreening, "001390.SZ",
    )
    assert category == "MULTIPLE_JSON_OBJECTS"


def test_provider_refusal_is_distinct_from_local_boundary_failure():
    category, _, _, _ = StructuredValidationProvider._validate_response(
        _response("{}", finish_reason="content_filter"), StructuredLightScreening, "001390.SZ",
    )
    assert category == "PROVIDER_CONTENT_POLICY_REFUSAL"
    category, path, _, _ = StructuredValidationProvider._validate_response(
        _response(json.dumps({**screening_example_fixture("001390.SZ"), "reason": "公司是全球龙头"}, ensure_ascii=False)),
        StructuredLightScreening, "001390.SZ",
    )
    assert category == "UNSUPPORTED_CLAIM"
    assert path == "$.reason"


def screening_example_fixture(code: str) -> dict:
    return {
        "stock_code": code, "screening_decision": "WATCH_ONLY", "llm_score": 1,
        "confidence": 0.1, "reason": "输入不足", "risk_note": "需复核",
        "data_conflict": False, "requires_manual_review": True,
        "evidence_fields": [], "missing_data": [],
    }


def test_failed_repair_is_audited_once(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("RUN_REAL_FUNDAMENTAL_RESEARCH", "true")
    provider = StructuredValidationProvider(QueueGateway([_response("not-json"), _response("still-not-json")]))
    with pytest.raises(Exception, match="INVALID_JSON"):
        provider.screening({"stock_code": "001390", "missing_fields": []}, run_mode="HISTORICAL_REPLAY", use_real_llm=True)
    assert len(provider.audit) == 1
    assert provider.audit[0]["diagnostics"]["repair_attempted"] is True
    assert provider.audit[0]["error_category"] == "INVALID_JSON"


def test_code_normalization_and_wire_adapter_unknowns():
    assert normalize_ts_code("000518") == "000518.SZ"
    assert normalize_ts_code("688130") == "688130.SH"
    assert normalize_ts_code("001390.SZ") == "001390.SZ"
    assert display_stock_code("688130.SH") == "688130"
    wire = FundamentalInferenceWireV3.model_validate(fundamental_wire_example("001390.SZ"))
    domain = fundamental_wire_to_domain(wire, {
        "financial_status": {"status": "STABLE"}, "missing_fields": [], "main_business": [],
        "manifest": {"decision_time": "2026-07-09T15:00:00+08:00"},
    })
    assert domain["stock_code"] == "001390.SZ"
    assert domain["industry_chain"]["chain_name"] == "UNKNOWN"
    assert domain["financial_status"]["status"] == "STABLE"
    assert domain["core_products"] == ["信息不足*"]


def test_input_quality_blocks_missing_main_business_and_reports_exact_fields():
    summary = summarize_structured_input({
        "stock_code": "000518", "company_profile": {"business_scope": "scope"},
        "main_business": [], "main_business_breakdown": {}, "level_one_sector": "制造业",
        "concept_tags": [], "financial_summary": {"end_date": "20260331", "revenue": 1},
        "financial_status": {"status": "STABLE"}, "quant": {"rank": 1, "total_score": 80},
    })
    assert summary["canonical_ts_code"] == "000518.SZ"
    assert summary["status"] == "INPUT_PROFILE_INCOMPLETE"
    assert summary["main_business_present"] is False
    assert summary["missing_required_fields"] == ["main_business"]


def test_profile_maps_structured_company_main_business_when_mainbz_cache_is_absent():
    profile = TushareFundamentalProfileBuilder().build(
        "000518.SZ", stock_basic={"name": "样本", "industry": "医药"},
        company={"main_business": "药品制造", "business_scope": "依法经营"},
        main_business=[], financial_selection={},
    )
    assert profile.main_business[0]["bz_item"] == "药品制造"
    assert profile.main_business_breakdown["P"]
    assert profile.data_coverage["main_business"] is True
