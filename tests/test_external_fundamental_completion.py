from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from database.base import Base
from database.models.research import ResearchEvidenceRecord, StockFundamentalProfile
from database.models.validation import (
    ModelValidationFailureAudit,
    ModelValidationLLMAudit,
    ModelValidationSample,
)
from database.session import create_engine_from_url, get_session
from fundamentals.external_evidence import (
    VerifiedFundamentalCompletionInput,
    VerifiedFundamentalCompletionService,
)


def _request() -> VerifiedFundamentalCompletionInput:
    profile = {
        "industry_chain_name": "暖通空调设备零部件产业链",
        "chain_position": "MIDSTREAM",
        "main_business_summary": "公司生产空调风叶、机械风机和高分子复合材料，面向暖通与设备散热应用。",
        "core_products": ["空调风叶", "机械风机", "高分子复合材料"],
        "concept_tags": ["暖通空调零部件"],
        "industry_position_level": "MAJOR_PARTICIPANT",
        "industry_position_description": "公开年报披露公司获得制造业单项冠军企业认定。",
        "structural_theme_fit": "节能风机和设备散热场景提供结构性增量方向。",
        "competitive_advantage": "多基地配套、空气动力研发和材料检测能力形成制造与交付基础。",
        "industry_trend": "传统空调需求趋缓，节能风机和复合材料仍有结构性拓展空间。",
        "investment_logic": "基本盘仍由成熟风叶和风机构成，新增业务的收入贡献需要持续验证。",
        "invalidation_conditions": ["传统业务继续下滑"],
        "domestic_substitution": "INSUFFICIENT_DATA",
        "observation_rating": "NORMAL_WATCH",
        "financial_status_explanation": "营业收入和扣非利润承压，非经常性收益抬高表观净利润。",
        "key_risks": ["客户集中", "存货风险"],
        "financial_snapshot": {"period": "2026Q1"},
        "verified_customers": ["示例客户"],
    }
    evidence = [
        {
            "title": "Company official website",
            "url": "https://example.com/company",
            "snippet": "Official company description of products, business and applications.",
            "source_tier": "tier_1",
            "source_type": "company_official_website",
            "credibility_score": 0.95,
            "related_fields": [
                "main_business_summary",
                "industry_chain",
                "core_products",
                "concept_tags",
                "competitive_advantage",
            ],
        },
        {
            "title": "Company annual report",
            "url": "https://example.com/annual.pdf",
            "published_at": "2026-04-24T00:00:00+08:00",
            "snippet": "Annual filing with financial statements, business analysis and risk factors.",
            "source_tier": "tier_1",
            "source_type": "company_filing",
            "credibility_score": 1.0,
            "related_fields": [
                "industry_position",
                "structural_theme_fit",
                "industry_trend",
                "investment_logic",
                "invalidation_conditions",
                "domestic_substitution",
                "financial_status_explanation",
                "key_risks",
                "financial_snapshot",
            ],
        },
    ]
    return VerifiedFundamentalCompletionInput.model_validate({
        "validation_run_id": "validation-1",
        "stock_code": "603726.SH",
        "as_of_time": "2026-07-13T16:00:00+08:00",
        "query": "verified company fundamentals",
        "profile": profile,
        "evidence": evidence,
    })


def _session():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    now = datetime.now(timezone.utc)
    session.add(ModelValidationSample(
        validation_run_id="validation-1",
        quant_run_id="quant-1",
        run_data_manifest_id="manifest-1",
        rank=1,
        stock_code="603726.SH",
        stock_name="朗迪集团",
        quant_scores={"total_score": 33.8},
        profile_version="structured-v1",
        selected_at=now,
        fundamental_result={"financial_status": {"status": "STABLE"}},
        screening_result={
            "llm_score": 46.25,
            "_trader_demo": {
                "execution_status": "FAILED",
                "errors": [{"task": "fundamental_structured_inference"}],
                "selection_source": "MANUAL",
            },
        },
        field_provenance={},
        missing_fields=["concept_tags"],
    ))
    session.add(ModelValidationLLMAudit(
        validation_run_id="validation-1",
        stock_code="603726.SH",
        task="fundamental_structured_inference",
        knowledge_mode="STRUCTURED_INPUT_ONLY",
        model_alias="light-screening-default",
        prompt_version="v4",
        status="FAILED",
        schema_status="FAILED",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        cache_status="MISS",
        error_category="UNSUPPORTED_CLAIM",
        diagnostics={},
    ))
    session.add(ModelValidationFailureAudit(
        validation_run_id="validation-1",
        stock_code="603726.SH",
        task="fundamental_structured_inference",
        attempt_id="attempt-1",
        prompt_version="v4",
        status="FAILED",
        error_category="UNSUPPORTED_CLAIM",
        error_field="$.investment_logic",
        error_message="no_unsupported_customer",
        diagnostics={},
    ))
    session.commit()
    return engine, session


def test_external_completion_is_audited_idempotent_and_does_not_rerun_flash():
    engine, session = _session()
    try:
        service = VerifiedFundamentalCompletionService(session)
        first = service.apply(_request())
        second = service.apply(_request())
        sample = session.scalar(select(ModelValidationSample))
        assert first["llm_calls"] == 0
        assert first["flash_rerun"] is False
        assert first["evidence_inserted"] == 2
        assert second["evidence_inserted"] == 0
        assert second["resolved_audit_count"] == 0
        assert sample.fundamental_result["analysis_status"] == "SUCCESS"
        assert sample.fundamental_result["research_mode"] == "EXTERNAL_VERIFIED"
        assert sample.fundamental_result["financial_status"]["status"] == "STABLE"
        assert sample.screening_result["llm_score"] == 46.25
        assert sample.screening_result["_trader_demo"]["execution_status"] == "SUCCESS"
        assert sample.missing_fields == []
        assert session.scalar(select(func.count()).select_from(ResearchEvidenceRecord)) == 2
        assert session.scalar(select(func.count()).select_from(StockFundamentalProfile)) == 1
        assert session.scalar(select(ModelValidationLLMAudit)).schema_status == "RESOLVED_HISTORY"
        assert session.scalar(select(ModelValidationFailureAudit)).status == "RESOLVED"
    finally:
        session.close()
        engine.dispose()


def test_external_completion_requires_https_and_complete_primary_source_coverage():
    payload = _request().model_dump(mode="json")
    payload["evidence"][0]["url"] = "http://example.com/company"
    with pytest.raises(ValidationError, match="requires HTTPS"):
        VerifiedFundamentalCompletionInput.model_validate(payload)

    payload = _request().model_dump(mode="json")
    for evidence in payload["evidence"]:
        evidence["related_fields"] = ["main_business_summary"]
    with pytest.raises(ValidationError, match="evidence coverage missing"):
        VerifiedFundamentalCompletionInput.model_validate(payload)
