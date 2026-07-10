from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from database.base import Base
from database.models.system import LLMUsage
from database.session import create_engine_from_url, get_session
from llm_gateway.router import build_request_hash
from llm_gateway.schemas import LLMResponse
from trader_demo.pro_resume import (
    PRO_CANDIDATE_PROMPT_VERSION,
    ProCandidateWireV2,
    ProResumeService,
    ProStageFailure,
    candidate_wire_schema,
    chunk_candidates,
    deterministic_review_order,
    portfolio_wire_schema,
)
from trader_demo.usage_ledger import AuthoritativeUsageLedger


def _candidate(code: str, score: float = 80) -> dict:
    return {
        "stock_code": code, "pro_score": score, "priority": "HIGH",
        "final_summary": "结构化候选评审，需人工复核。",
        "key_strengths": ["量化与二筛方向一致"], "key_risks": ["未外部核验"],
        "fundamental_quality": "MEDIUM", "quant_llm_consistency": "HIGH",
        "manual_review_priority": "MEDIUM", "data_conflict": False,
    }


def _wire(codes: list[str], chunk_id: str = "candidate-01") -> dict:
    return {
        "schema_version": "pro_candidate_wire_v2", "chunk_id": chunk_id,
        "results": [_candidate(code, 90 - index) for index, code in enumerate(codes)],
    }


def _response(payload: dict | str, *, tokens=(100, 20)) -> LLMResponse:
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return LLMResponse(
        provider="deepseek", model="deepseek-v4-pro", model_alias="controller-high-capability",
        content=content, input_tokens=tokens[0], output_tokens=tokens[1],
        total_tokens=sum(tokens), cost_usd=0.01, latency_ms=12,
        request_hash="request", status="ok", finish_reason="stop",
        thinking_mode="enabled", reasoning_effort="high",
        raw_response_metadata={"http_status": 200, "response_id": "safe-id"},
    )


def test_wire_schemas_are_shallow_without_refs_or_anyof():
    for schema in (candidate_wire_schema(), portfolio_wire_schema()):
        text = json.dumps(schema)
        assert "$ref" not in text
        assert "anyOf" not in text
        assert "$defs" not in text


@pytest.mark.parametrize("codes", [
    ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"],
    ["301356.SZ"],
])
def test_candidate_chunk_accepts_five_or_one_exact_codes(codes):
    checked = ProResumeService._validate_candidate(_response(_wire(codes)), "candidate-01", codes)
    assert isinstance(checked.value, ProCandidateWireV2)
    assert checked.diagnostics["schema_status"] == "PASS"


def test_candidate_extra_missing_or_duplicate_stock_is_rejected():
    expected = ["000001.SZ", "000002.SZ"]
    for actual in (["000001.SZ", "000003.SZ"], ["000001.SZ", "000001.SZ"]):
        checked = ProResumeService._validate_candidate(_response(_wire(actual)), "candidate-01", expected)
        assert checked.value is None
        assert checked.diagnostics["error_category"] == "CANDIDATE_CODE_SET_MISMATCH"


@pytest.mark.parametrize("field,value", [("pro_score", 101), ("priority", "URGENT"), ("fundamental_quality", "UNKNOWN")])
def test_candidate_score_and_enums_are_strict(field, value):
    payload = _wire(["000001.SZ"])
    payload["results"][0][field] = value
    checked = ProResumeService._validate_candidate(_response(payload), "candidate-01", ["000001.SZ"])
    assert checked.value is None
    assert checked.diagnostics["error_category"] == "SCHEMA_ERROR"


def test_portfolio_rejects_extra_stock_and_price_fields():
    payload = {
        "schema_version": "pro_portfolio_wire_v2", "overall_summary": "需人工复核。",
        "portfolio_risk_level": "REVIEW_REQUIRED", "sector_concentration_notes": [],
        "industry_chain_concentration_notes": [], "manual_pool_notes": [],
        "top_priority_stock_codes": ["600000.SH"], "key_portfolio_risks": [],
        "manual_review_focus": [], "data_conflict": False,
    }
    checked = ProResumeService._validate_portfolio(_response(payload), {"000001.SZ"})
    assert checked.value is None
    assert checked.diagnostics["error_category"] == "PORTFOLIO_EXTRA_STOCK"
    payload["top_priority_stock_codes"] = []
    payload["recommended_price"] = 10
    checked = ProResumeService._validate_portfolio(_response(payload), {"000001.SZ"})
    assert checked.diagnostics["error_category"] == "FORBIDDEN_PRICE_OR_POSITION_FIELD"


def test_local_ranking_tie_break_is_stable_and_keeps_every_review():
    samples = {
        "000001.SZ": SimpleNamespace(rank=2, screening_result={"llm_score": 80}),
        "000002.SZ": SimpleNamespace(rank=1, screening_result={"llm_score": 80}),
        "000003.SZ": SimpleNamespace(rank=3, screening_result={"llm_score": 90}),
    }
    reviews = [
        SimpleNamespace(stock_code="000001.SZ", pro_score=80, manual_review_priority="MEDIUM"),
        SimpleNamespace(stock_code="000002.SZ", pro_score=80, manual_review_priority="HIGH"),
        SimpleNamespace(stock_code="000003.SZ", pro_score=80, manual_review_priority="MEDIUM"),
    ]
    ordered = deterministic_review_order(reviews, samples)
    assert [row.stock_code for row in ordered] == ["000002.SZ", "000003.SZ", "000001.SZ"]
    assert len(ordered) == len(reviews)
    assert not hasattr(ordered[0], "pro_rank")


def test_chunking_preserves_stable_candidate_order_and_manual_items():
    samples = [
        SimpleNamespace(stock_code=f"00000{i}.SZ", rank=i, screening_result={
            "llm_score": 80 - i,
            "_trader_demo": {"selection_source": "MANUAL" if i > 5 else "LLM_TOP20"},
        })
        for i in range(1, 8)
    ]
    chunks = chunk_candidates(samples, 5)
    assert [len(chunk) for chunk in chunks] == [5, 2]
    assert {sample.stock_code for chunk in chunks for sample in chunk} == {sample.stock_code for sample in samples}


def test_usage_is_persisted_before_schema_failure_and_repair_is_separate():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    responses = [_response({"bad": True}, tokens=(100, 20)), _response(_wire(["000001.SZ"]), tokens=(110, 25))]

    class Gateway:
        def chat(self, _request):
            return responses.pop(0)

    resume = SimpleNamespace(run_id="pro-resume-test", flash_validation_run_id="flash-test")
    service = ProResumeService(session, AuthoritativeUsageLedger(engine), Gateway())
    result, report = service._candidate_call(
        resume, "candidate-01", [{"stock_code": "000001.SZ"}], ["000001.SZ"],
        model_alias="controller-high-capability",
    )
    rows = list(session.scalars(select(LLMUsage).order_by(LLMUsage.id)))
    assert result is not None
    assert report["repair_attempted"] is True
    assert len(rows) == 2
    assert rows[0].status == "FAILED"
    assert rows[1].status == "SUCCESS"
    assert rows[0].total_tokens == 120 and rows[1].total_tokens == 135
    assert rows[1].task.endswith("_repair")
    session.close()


def test_repair_failure_keeps_both_usage_rows():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    responses = [_response("not-json"), _response("still-not-json")]

    class Gateway:
        def chat(self, _request):
            return responses.pop(0)

    service = ProResumeService(session, AuthoritativeUsageLedger(engine), Gateway())
    result, report = service._candidate_call(
        SimpleNamespace(run_id="pro-resume-fail", flash_validation_run_id="flash-test"),
        "candidate-01", [{"stock_code": "000001.SZ"}], ["000001.SZ"],
        model_alias="controller-high-capability",
    )
    assert result is None and report["schema_status"] == "FAILED"
    assert len(list(session.scalars(select(LLMUsage)))) == 2
    session.close()


def test_new_prompt_version_changes_request_hash():
    request = ProResumeService._candidate_request(
        "candidate-01", [{"stock_code": "000001.SZ"}], "controller-high-capability"
    )
    assert request.prompt_version == PRO_CANDIDATE_PROMPT_VERSION
    changed = request.model_copy(update={"prompt_version": "pro_candidate_review_v3"})
    assert build_request_hash(request) != build_request_hash(changed)


def test_failed_chunk_resume_is_blocked_without_another_provider_call():
    class Gateway:
        def chat(self, _request):
            raise AssertionError("provider must not be called")

    resume = SimpleNamespace(
        status="PARTIAL_PRO_FAILURE",
        config_snapshot={"chunk_reports": [{"chunk_id": "candidate-01", "schema_status": "FAILED"}]},
    )
    service = ProResumeService(SimpleNamespace(), SimpleNamespace(), Gateway())
    with pytest.raises(ProStageFailure, match="FAILED_CHUNK_REQUIRES_NEW_PRO_RESUME_RUN"):
        service.run_candidate_chunks(
            resume, [], selection_sources={}, chunks=[], model_alias="controller-high-capability"
        )
