from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from database.base import Base
from database.models.system import LLMUsage
from database.models.validation import ProCandidateReview
from database.session import create_engine_from_url, get_session
from llm_gateway.schemas import LLMResponse
from scripts.run_daily_full_pipeline_excel import (
    _copy_v3_success_reviews,
    _merge_v3_review_reports,
    _v3_single_review_metrics,
)
from trader_demo.pro_single_v3 import (
    PORTFOLIO_CONTRACT_VERSION,
    SINGLE_CONTRACT_VERSION,
    SINGLE_PROMPT_VERSION,
    ProCandidateSingleWireV3,
    ProSingleV3Failure,
    ProSingleV3Service,
    _single_input,
    _validate_portfolio,
    _validate_single,
    deterministic_v3_review_order,
    portfolio_v3_schema,
    select_v3_canary,
    single_wire_schema,
    stable_v3_order,
)
from trader_demo.usage_ledger import AuthoritativeUsageLedger


def _candidate(code: str = "603019.SH", **updates) -> dict:
    payload = {
        "schema_version": SINGLE_CONTRACT_VERSION,
        "stock_code": code,
        "pro_score": 82,
        "priority": "HIGH",
        "final_summary": "结构化审核结果，仅供人工复核。",
        "key_strengths": ["量化与二筛方向一致"],
        "key_risks": ["基本面字段仍需核验"],
        "fundamental_quality": "MEDIUM",
        "quant_llm_consistency": "HIGH",
        "manual_review_priority": "MEDIUM",
        "data_conflict": False,
    }
    payload.update(updates)
    return payload


def _portfolio(codes: list[str]) -> dict:
    return {
        "schema_version": PORTFOLIO_CONTRACT_VERSION,
        "overall_summary": "组合结果仅供人工复核。",
        "portfolio_risk_level": "REVIEW_REQUIRED",
        "sector_concentration_notes": [],
        "industry_chain_concentration_notes": [],
        "manual_pool_notes": [],
        "top_priority_stock_codes": codes,
        "key_portfolio_risks": [],
        "manual_review_focus": [],
        "data_conflict": False,
    }


def _response(
    payload: dict | str,
    *,
    finish_reason: str = "stop",
    thinking_mode: str = "disabled",
    model: str = "deepseek-v4-pro",
    tokens: tuple[int, int] = (100, 20),
) -> LLMResponse:
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return LLMResponse(
        provider="deepseek",
        model=model,
        model_alias="controller-high-capability",
        content=content,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        total_tokens=sum(tokens),
        cost_usd=0.01,
        latency_ms=12,
        request_hash="request",
        status="ok",
        finish_reason=finish_reason,
        thinking_mode=thinking_mode,
        reasoning_effort=None,
        raw_response_metadata={"http_status": 200, "response_id": "safe-id", "reasoning_tokens": 0},
    )


def _sample(
    code: str,
    *,
    rank: int = 1,
    flash_score: float = 80,
    source: str = "LLM_TOP20",
    manual: bool = False,
):
    return SimpleNamespace(
        stock_code=code,
        stock_name=f"测试{code}",
        rank=rank,
        quant_scores={"total_score": 75, "technical_score": 70, "risk_score": 20},
        screening_result={
            "llm_score": flash_score,
            "screening_decision": "OBSERVE",
            "confidence": 0.7,
            "_trader_demo": {
                "selection_source": source,
                "manual_selected": manual,
                "manual_reason": "人工池",
            },
        },
        fundamental_result={
            "financial_status": {"status": "NORMAL"},
            "observation_rating": "OBSERVE",
            "industry_chain": {"chain_name": "测试产业链"},
            "core_products": ["产品A"],
        },
        field_provenance={"level_one_sector": {"value": "测试行业"}},
        missing_fields=[],
    )


def _review_row(run_id: str, code: str, input_hash: str) -> ProCandidateReview:
    return ProCandidateReview(
        pro_resume_run_id=run_id,
        flash_validation_run_id="flash-test",
        chunk_id="single",
        contract_version=SINGLE_CONTRACT_VERSION,
        candidate_input_hash=input_hash,
        stock_code=code,
        pro_score=Decimal("82"),
        pro_rank=None,
        priority="HIGH",
        final_summary="仅供人工复核。",
        key_strengths=[],
        key_risks=[],
        fundamental_quality="MEDIUM",
        quant_llm_consistency="HIGH",
        manual_review_priority="MEDIUM",
        data_conflict=False,
        prompt_version=SINGLE_PROMPT_VERSION,
        actual_model="deepseek-v4-pro",
        review_status="SUCCESS",
    )


def test_v3_wire_schemas_are_shallow_and_single_output_is_valid():
    for schema in (single_wire_schema(), portfolio_v3_schema()):
        serialized = json.dumps(schema)
        assert "$ref" not in serialized
        assert "$defs" not in serialized
        assert "anyOf" not in serialized
    checked = _validate_single(_response(_candidate()), "603019.SH", 160)
    assert isinstance(checked.value, ProCandidateSingleWireV3)
    assert checked.diagnostics["schema_status"] == "PASS"


@pytest.mark.parametrize(
    ("updates", "category"),
    [
        ({"stock_code": "600000.SH"}, "STOCK_CODE_MISMATCH"),
        ({"pro_score": 101}, "SCHEMA_ERROR"),
        ({"key_strengths": ["a", "b", "c", "d"]}, "SCHEMA_ERROR"),
        ({"recommended_price": 10}, "FORBIDDEN_PRICE_OR_POSITION_FIELD"),
        ({"unexpected": True}, "SCHEMA_ERROR"),
    ],
)
def test_v3_single_rejects_code_score_array_extra_and_price_fields(updates, category):
    checked = _validate_single(_response(_candidate(**updates)), "603019.SH", 160)
    assert checked.value is None
    assert checked.diagnostics["error_category"] == category


def test_v3_single_handles_fence_safe_tail_and_truncation_locally():
    payload = json.dumps(_candidate(), ensure_ascii=False)
    fenced = _validate_single(_response(f"```json\n{payload}\n```"), "603019.SH", 160)
    tailed = _validate_single(_response(payload + "\n已完成"), "603019.SH", 160)
    truncated = _validate_single(
        _response(payload[:-5], finish_reason="length"), "603019.SH", 160
    )
    assert fenced.value is not None
    assert tailed.value is not None
    assert tailed.diagnostics["trailing_text_removed"] is True
    assert truncated.value is None
    assert truncated.diagnostics["error_category"] == "JSON_TRUNCATED"


def test_v3_requires_resolved_pro_model_disabled_thinking_and_stop_finish():
    assert _validate_single(
        _response(_candidate(), thinking_mode="enabled"), "603019.SH", 160
    ).diagnostics["error_category"] == "PRO_V3_RESOLVED_MODEL_CONFIG_MISMATCH"
    assert _validate_single(
        _response(_candidate(), model="deepseek-v4-flash"), "603019.SH", 160
    ).diagnostics["error_category"] == "PRO_V3_RESOLVED_MODEL_CONFIG_MISMATCH"
    assert _validate_single(
        _response(_candidate(), finish_reason="content_filter"), "603019.SH", 160
    ).diagnostics["error_category"] == "PRO_V3_UNEXPECTED_FINISH_REASON"


def test_v3_requests_explicitly_disable_thinking_and_use_json_response_mode():
    service = ProSingleV3Service(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    candidate = service._candidate_request({"stock_code": "603019.SH"}, retry=False, max_tokens=1800)
    portfolio = service._portfolio_request({"candidates": [], "computed_statistics": {}}, retry=False)
    for request, max_tokens in ((candidate, 1800), (portfolio, 3200)):
        assert request.thinking_mode == "disabled"
        assert request.reasoning_effort is None
        assert request.allow_fallback is False
        assert request.metadata["structured"] is True
        assert request.max_tokens == max_tokens


def test_v3_unsupported_claim_repair_explicitly_removes_unverified_claims():
    service = ProSingleV3Service(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    request = service._candidate_repair_request(
        "300628.SZ",
        json.dumps(_candidate("300628.SZ"), ensure_ascii=False),
        {"error_category": "UNSUPPORTED_CLAIM", "schema_error_paths": ["$.final_summary"]},
        1800,
    )
    payload = json.loads(request.messages[1].content)
    rules = " ".join(payload["repair_rules"])
    assert "market-share" in rules
    assert "named-customer" in rules
    assert "current-information" in rules
    assert payload["error_category"] == "UNSUPPORTED_CLAIM"


def test_v3_canary_and_candidate_order_are_deterministic_and_distinct():
    samples = [
        _sample("603019.SH", rank=5, flash_score=90, source="BOTH", manual=True),
        _sample("000001.SZ", rank=1, flash_score=99, source="LLM_TOP20"),
        _sample("000002.SZ", rank=100, flash_score=40, source="MANUAL", manual=True),
        _sample("000003.SZ", rank=2, flash_score=85, source="MANUAL", manual=True),
    ]
    ordered = stable_v3_order(samples)
    assert [row.stock_code for row in ordered] == ["603019.SH", "000003.SZ", "000002.SZ", "000001.SZ"]
    assert [row.stock_code for row in select_v3_canary(samples)] == [
        "603019.SH", "000001.SZ", "000002.SZ"
    ]


def test_v3_local_ranking_uses_all_tie_breaks_without_mutation():
    samples = {
        "000001.SZ": _sample("000001.SZ", rank=2, flash_score=90),
        "000002.SZ": _sample("000002.SZ", rank=1, flash_score=80),
        "000003.SZ": _sample("000003.SZ", rank=3, flash_score=95),
    }
    reviews = [
        SimpleNamespace(stock_code="000001.SZ", pro_score=80, priority="MEDIUM", manual_review_priority="HIGH"),
        SimpleNamespace(stock_code="000002.SZ", pro_score=80, priority="HIGH", manual_review_priority="LOW"),
        SimpleNamespace(stock_code="000003.SZ", pro_score=80, priority="MEDIUM", manual_review_priority="HIGH"),
    ]
    ordered = deterministic_v3_review_order(reviews, samples)
    assert [row.stock_code for row in ordered] == ["000002.SZ", "000003.SZ", "000001.SZ"]
    assert len(ordered) == len(reviews)
    assert not hasattr(ordered[0], "pro_rank")


def test_v3_portfolio_rejects_extra_stock_price_and_truncation():
    valid = _validate_portfolio(_response(_portfolio(["603019.SH"])), {"603019.SH"})
    assert valid.value is not None
    extra = _validate_portfolio(_response(_portfolio(["600000.SH"])), {"603019.SH"})
    assert extra.diagnostics["error_category"] == "PORTFOLIO_EXTRA_STOCK"
    with_price = _portfolio([])
    with_price["position_percent"] = 10
    assert _validate_portfolio(
        _response(with_price), {"603019.SH"}
    ).diagnostics["error_category"] == "FORBIDDEN_PRICE_OR_POSITION_FIELD"
    truncated = _validate_portfolio(
        _response('{"schema_version":', finish_reason="length"), {"603019.SH"}
    )
    assert truncated.diagnostics["error_category"] == "JSON_TRUNCATED"


def test_v3_usage_is_saved_before_schema_repair_and_reasoning_is_not_stored():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    responses = [_response({"bad": True}, tokens=(100, 20)), _response(_candidate(), tokens=(110, 25))]
    requests = []

    class Gateway:
        def chat(self, request):
            requests.append(request)
            return responses.pop(0)

    ledger = AuthoritativeUsageLedger(engine)
    service = ProSingleV3Service(session, ledger, Gateway())
    result = service._review_one(
        SimpleNamespace(run_id="pro-v3-test", flash_validation_run_id="flash-test"),
        _sample("603019.SH"),
        {"603019.SH": "LLM_TOP20"},
        "CANARY",
    )
    rows = list(session.scalars(select(LLMUsage).order_by(LLMUsage.id)))
    assert result.value is not None
    assert result.report["repair_attempted"] is True
    assert len(rows) == 2
    assert rows[0].status == "FAILED" and rows[1].status == "SUCCESS"
    assert rows[0].total_tokens == 120 and rows[1].total_tokens == 135
    assert rows[1].task == "pro_candidate_single_review_repair"
    assert all(request.thinking_mode == "disabled" for request in requests)
    assert all(row.response_metadata["reasoning_stored"] is False for row in rows)
    assert all("content" not in row.response_metadata for row in rows)
    service._persist_review(
        SimpleNamespace(run_id="pro-v3-test", flash_validation_run_id="flash-test"),
        result.value,
        result.report,
        result.input_hash,
    )
    metrics = _v3_single_review_metrics(session, "pro-v3-test", 1)
    assert metrics["success"] == 1
    assert metrics["new_calls"] == 1
    assert metrics["repair_calls"] == 1
    assert metrics["reused"] == 0
    assert metrics["input_tokens"] == 210
    assert metrics["output_tokens"] == 45
    session.close()


def test_v3_length_retry_is_single_stock_and_uses_2400_tokens():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    responses = [
        _response('{"schema_version":', finish_reason="length"),
        _response(_candidate()),
    ]
    requests = []

    class Gateway:
        def chat(self, request):
            requests.append(request)
            return responses.pop(0)

    service = ProSingleV3Service(session, AuthoritativeUsageLedger(engine), Gateway())
    result = service._review_one(
        SimpleNamespace(run_id="pro-v3-length", flash_validation_run_id="flash-test"),
        _sample("603019.SH"),
        {"603019.SH": "BOTH"},
        "CANARY",
    )
    assert result.value is not None
    assert [request.max_tokens for request in requests] == [1800, 2400]
    retry_payload = json.loads(requests[1].messages[1].content)
    assert retry_payload["candidate"]["stock_code"] == "603019.SH"
    assert "candidates" not in retry_payload
    session.close()


def test_v3_successful_review_is_reused_only_when_input_hash_matches(tmp_path):
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    sample = _sample("603019.SH")
    compact, _ = _single_input(sample, {"603019.SH": "LLM_TOP20"})
    input_hash = hashlib.sha256(
        json.dumps(compact, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    session.add(_review_row("pro-v3-resume", "603019.SH", input_hash))
    session.commit()

    class Gateway:
        def chat(self, _request):
            raise AssertionError("successful review must not call provider again")

    service = ProSingleV3Service(session, AuthoritativeUsageLedger(engine), Gateway())
    resume = SimpleNamespace(run_id="pro-v3-resume", config_snapshot={}, status="RUNNING")
    reports = service.run_reviews(
        resume,
        [sample],
        {"603019.SH": "LLM_TOP20"},
        tmp_path / "unused.json",
        stage="FULL",
        stop_on_failure=True,
    )
    assert reports[0]["usage_source"] == "RESUME_REUSED"
    sample.screening_result["llm_score"] = 1
    with pytest.raises(ValueError, match="PRO_V3_CANDIDATE_INPUT_HASH_MISMATCH"):
        service.run_reviews(
            resume,
            [sample],
            {"603019.SH": "LLM_TOP20"},
            tmp_path / "unused.json",
            stage="FULL",
            stop_on_failure=True,
        )
    session.close()


def test_v3_failed_run_successes_copy_to_new_run_without_usage_duplication():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    session.add(_review_row("pro-v3-old", "603019.SH", "a" * 64))
    session.commit()
    assert _copy_v3_success_reviews(session, "pro-v3-old", "pro-v3-new") == 1
    session.commit()
    copied = session.scalar(select(ProCandidateReview).where(
        ProCandidateReview.pro_resume_run_id == "pro-v3-new"
    ))
    assert copied is not None
    assert copied.candidate_input_hash == "a" * 64
    assert copied.pro_rank is None
    assert list(session.scalars(select(LLMUsage))) == []
    session.close()


def test_v3_canary_failure_stops_expansion_and_report_merge_keeps_actual_usage():
    service = object.__new__(ProSingleV3Service)
    service.run_reviews = lambda *args, **kwargs: [
        {"schema_status": "PASS"},
        {"schema_status": "FAILED", "stock_code": "000001.SZ"},
        {"schema_status": "PASS"},
    ]
    with pytest.raises(ProSingleV3Failure, match="PRO_V3_CANARY_FAILED"):
        service.run_canary(SimpleNamespace(), [1, 2, 3], {}, SimpleNamespace())

    candidates = [_sample("603019.SH"), _sample("000001.SZ")]
    canary = [
        {"stock_code": "603019.SH", "usage_source": "CURRENT_CALL", "input_tokens": 10},
    ]
    full = [
        {"stock_code": "603019.SH", "usage_source": "RESUME_REUSED", "input_tokens": 0},
        {"stock_code": "000001.SZ", "usage_source": "CURRENT_CALL", "input_tokens": 20},
    ]
    merged = _merge_v3_review_reports(candidates, canary, full)
    assert [item["input_tokens"] for item in merged] == [10, 20]
