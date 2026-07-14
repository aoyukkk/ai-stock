from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stock_codes import normalize_ts_code


class FundamentalInferenceWireV3(BaseModel):
    """Flat provider wire contract; the richer domain model stays local."""

    model_config = ConfigDict(extra="forbid")

    stock_code: str
    industry_chain_name: str
    chain_position: Literal[
        "UPSTREAM_RESOURCE", "UPSTREAM_MATERIAL", "MIDSTREAM_COMPONENT",
        "MIDSTREAM_EQUIPMENT", "DOWNSTREAM_PRODUCT", "DOWNSTREAM_APPLICATION",
        "SERVICE_PLATFORM", "MULTI_SEGMENT", "UNKNOWN",
    ]
    direct_or_indirect: Literal["DIRECT", "INDIRECT", "UNKNOWN"]
    level_one_sector_explanation: str
    main_business_summary: str
    industry_position: Literal["MAJOR_PARTICIPANT", "SECOND_TIER", "NICHE_PLAYER", "UNCLEAR"]
    industry_position_description: str
    core_products: list[str]
    concept_tags: list[str]
    competitive_advantage: str
    industry_trend: str
    investment_logic: str
    invalidation_conditions: list[str]
    domestic_substitution_level: Literal["NONE", "WEAK", "MODERATE", "INSUFFICIENT_DATA"]
    domestic_substitution_target: str
    observation_rating: Literal[
        "CORE_TRACK", "KEY_WATCH", "NORMAL_WATCH", "LOW_PRIORITY", "AVOID", "INSUFFICIENT_DATA",
    ]
    confidence: float = Field(ge=0, le=1)
    data_conflict: bool
    evidence_fields: list[str]
    missing_fields: list[str]


class FundamentalEnrichmentWireV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["fundamental_enrichment_wire_v4"]
    stock_code: str
    core_products: list[str] = Field(min_length=1, max_length=20)
    industry_chain_name: str = Field(min_length=2, max_length=120)
    chain_position: Literal[
        "UPSTREAM", "MIDSTREAM", "DOWNSTREAM", "SERVICE_PLATFORM", "MULTI_SEGMENT"
    ]
    normalized_concept_tags: list[str] = Field(max_length=12)
    inferred_concept_tags: list[str] = Field(max_length=6)
    industry_position: Literal["MAJOR_PARTICIPANT", "SECOND_TIER", "NICHE_PLAYER", "UNCLEAR"]
    industry_position_description: str
    structural_theme_fit: str
    competitive_advantage: str
    structural_industry_trend: str
    investment_logic: str
    invalidation_conditions: list[str] = Field(min_length=1, max_length=8)
    domestic_substitution: Literal["NONE", "WEAK", "MODERATE", "INSUFFICIENT_DATA"]
    observation_rating: Literal[
        "CORE_TRACK", "KEY_WATCH", "NORMAL_WATCH", "LOW_PRIORITY", "AVOID", "INSUFFICIENT_DATA"
    ]
    financial_status_explanation: str
    confidence: float = Field(ge=0, le=0.5)
    data_conflict: bool
    evidence_fields: list[str] = Field(max_length=20)
    missing_fields: list[str] = Field(max_length=20)


class FlashComponentWireV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["flash_component_wire_v4"]
    stock_code: str
    quant_consistency_score: float = Field(ge=0, le=100)
    fundamental_quality_score: float = Field(ge=0, le=100)
    financial_quality_score: float = Field(ge=0, le=100)
    risk_fit_score: float = Field(ge=0, le=100)
    data_quality_score: float = Field(ge=0, le=100)
    data_quality_penalty: float = Field(ge=0, le=100)
    risk_penalty: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    reason: str
    risk_note: str
    data_conflict: bool
    requires_manual_review: bool = True
    evidence_fields: list[str] = Field(max_length=20)
    missing_data: list[str] = Field(max_length=20)


def flash_component_v4_example(stock_code: str = "000000.SZ") -> dict[str, Any]:
    return {
        "schema_version": "flash_component_wire_v4",
        "stock_code": stock_code,
        "quant_consistency_score": 73,
        "fundamental_quality_score": 61,
        "financial_quality_score": 57,
        "risk_fit_score": 68,
        "data_quality_score": 82,
        "data_quality_penalty": 3,
        "risk_penalty": 5,
        "confidence": 0.64,
        "reason": "示例：必须依据当前股票的结构化输入分别评价组件，不能照抄这些数值。",
        "risk_note": "示例：说明结构化数据中可见的具体风险与缺失项。",
        "data_conflict": False,
        "requires_manual_review": True,
        "evidence_fields": [],
        "missing_data": [],
    }


def fundamental_v4_example(stock_code: str = "000000.SZ") -> dict[str, Any]:
    return {
        "schema_version": "fundamental_enrichment_wire_v4",
        "stock_code": stock_code,
        "core_products": ["从主营输入提取的产品或服务"],
        "industry_chain_name": "根据主营归纳的宽泛产业链",
        "chain_position": "MULTI_SEGMENT",
        "normalized_concept_tags": [],
        "inferred_concept_tags": ["保守推断标签*"],
        "industry_position": "UNCLEAR",
        "industry_position_description": "缺少外部排名证据，仅依据主营结构判断。",
        "structural_theme_fit": "仅描述与主营相关的结构性方向。",
        "competitive_advantage": "可能具备相关能力，但仍需核验。",
        "structural_industry_trend": "仅描述长期结构性趋势。",
        "investment_logic": "仅依据输入主营、财务和量化摘要归纳。",
        "invalidation_conditions": ["主营或财务规则状态显著恶化"],
        "domestic_substitution": "INSUFFICIENT_DATA",
        "observation_rating": "NORMAL_WATCH",
        "financial_status_explanation": "沿用规则财务状态并解释可见指标。",
        "confidence": 0.3,
        "data_conflict": False,
        "evidence_fields": ["main_business"],
        "missing_fields": [],
    }


def fundamental_v4_to_domain(
    wire: FundamentalEnrichmentWireV4, context: dict[str, Any]
) -> dict[str, Any]:
    source_tags = [str(value).strip().rstrip("*") for value in context.get("concept_tags") or [] if str(value).strip()]
    verified = str(context.get("concept_source_status") or "UNKNOWN") == "VERIFIED_STRUCTURED"
    concepts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in [*source_tags, *wire.normalized_concept_tags]:
        name = str(value).strip().rstrip("*")
        if not name or name in seen:
            continue
        seen.add(name)
        concepts.append({
            "name": name,
            "source_status": "VERIFIED_STRUCTURED" if verified and name in source_tags else "LLM_UNVERIFIED",
            "display_marker": "" if verified and name in source_tags else "*",
        })
    for value in wire.inferred_concept_tags[:6]:
        name = str(value).strip().rstrip("*")
        if not name or name in seen:
            continue
        seen.add(name)
        concepts.append({"name": name, "source_status": "LLM_UNVERIFIED", "display_marker": "*"})
    concept_audit = dict(context.get("concept_mapping_audit") or {})
    inferred_count = len({str(value).strip().rstrip("*") for value in wire.inferred_concept_tags if str(value).strip()})
    unverified_count = sum(item["source_status"] == "LLM_UNVERIFIED" for item in concepts)
    if verified and unverified_count:
        source_status = "MIXED_VERIFIED_AND_LLM_UNVERIFIED"
    elif verified:
        source_status = "VERIFIED_STRUCTURED"
    elif unverified_count:
        source_status = "LLM_UNVERIFIED"
    else:
        source_status = "UNKNOWN"
    concept_audit.update({
        "inferred_concept_tag_count": inferred_count,
        "final_concept_tag_count": len(concepts),
        "concept_source_status": source_status,
    })
    confidence = min(float(wire.confidence), 0.5)
    generic = lambda value: {
        "summary": value, "source_status": "LLM_UNVERIFIED", "confidence": confidence,
        "display_marker": "*",
    }
    return {
        "stock_code": normalize_ts_code(wire.stock_code),
        "as_of_time": str((context.get("manifest") or {}).get("decision_time") or "1970-01-01T00:00:00+00:00"),
        "research_mode": "DEEPSEEK_UNVERIFIED",
        "analysis_status": "SUCCESS",
        "wire_schema_version": "fundamental_enrichment_wire_v4",
        "industry_chain": {
            "chain_name": wire.industry_chain_name,
            "chain_position": wire.chain_position,
            "source_status": "LLM_UNVERIFIED", "confidence": confidence,
            "evidence_fields": list(wire.evidence_fields), "display_marker": "*",
        },
        "main_business_summary": generic(_main_business_text(context)),
        "core_products": list(wire.core_products),
        "concept_tags": concepts,
        "normalized_concept_tags": list(wire.normalized_concept_tags),
        "inferred_concept_tags": list(wire.inferred_concept_tags),
        "concept_mapping_audit": concept_audit,
        "industry_position": {
            "level": wire.industry_position, "description": wire.industry_position_description,
            "source_status": "LLM_UNVERIFIED", "confidence": confidence, "display_marker": "*",
        },
        "structural_theme_fit": {
            "value": wire.structural_theme_fit, "source_status": "LLM_UNVERIFIED",
            "confidence": confidence, "display_marker": "*",
        },
        "competitive_advantage": generic(wire.competitive_advantage),
        "industry_trend": generic(wire.structural_industry_trend),
        "investment_logic": generic(wire.investment_logic),
        "invalidation_conditions": list(wire.invalidation_conditions),
        "domestic_substitution": {
            "level": wire.domestic_substitution, "source_status": "LLM_UNVERIFIED",
            "confidence": confidence, "display_marker": "*",
        },
        "observation_rating": wire.observation_rating,
        "financial_status": context.get("financial_status") or {},
        "financial_status_explanation": generic(wire.financial_status_explanation),
        "data_conflict": wire.data_conflict,
        "missing_fields": sorted(set(wire.missing_fields) | set(context.get("missing_fields") or [])),
        "requires_manual_review": True,
        "display_marker": "*",
    }


def _main_business_text(context: dict[str, Any]) -> str:
    values = []
    for item in context.get("main_business") or []:
        if isinstance(item, dict):
            value = item.get("bz_item") or item.get("main_business")
        else:
            value = item
        if value:
            values.append(str(value))
    return "；".join(values[:8]) or str((context.get("company_profile") or {}).get("business_scope") or "信息不足*")


def fundamental_wire_example(stock_code: str = "000000.SZ") -> dict[str, Any]:
    return {
        "stock_code": stock_code,
        "industry_chain_name": "UNKNOWN",
        "chain_position": "UNKNOWN",
        "direct_or_indirect": "UNKNOWN",
        "level_one_sector_explanation": "仅依据输入行业字段，无法进一步确认。",
        "main_business_summary": "仅概括输入中的主营业务。",
        "industry_position": "UNCLEAR",
        "industry_position_description": "输入不足，无法确认行业地位。",
        "core_products": [],
        "concept_tags": [],
        "competitive_advantage": "UNKNOWN",
        "industry_trend": "UNKNOWN",
        "investment_logic": "仅依据输入数据观察，需人工复核。",
        "invalidation_conditions": ["输入数据发生重大变化"],
        "domestic_substitution_level": "INSUFFICIENT_DATA",
        "domestic_substitution_target": "UNKNOWN",
        "observation_rating": "INSUFFICIENT_DATA",
        "confidence": 0.1,
        "data_conflict": False,
        "evidence_fields": [],
        "missing_fields": [],
    }


def fundamental_wire_to_domain(wire: FundamentalInferenceWireV3, context: dict[str, Any]) -> dict[str, Any]:
    confidence = min(float(wire.confidence), 0.4)
    evidence = list(wire.evidence_fields)
    missing = sorted(set(wire.missing_fields) | set(context.get("missing_fields") or []))
    verified_main = context.get("main_business") or []
    return {
        "stock_code": normalize_ts_code(wire.stock_code),
        "as_of_time": str((context.get("manifest") or {}).get("decision_time") or "1970-01-01T00:00:00+00:00"),
        "research_mode": "DEEPSEEK_UNVERIFIED",
        "analysis_status": "SUCCESS",
        "wire_schema_version": "fundamental-inference-wire-v3",
        "industry_chain": {
            "chain_name": wire.industry_chain_name,
            "chain_position": wire.chain_position,
            "direct_or_indirect": wire.direct_or_indirect,
            "source_status": "LLM_UNVERIFIED",
            "confidence": confidence,
            "reason": wire.industry_position_description,
            "evidence_fields": evidence,
        },
        "level_one_sector_explanation": _generic(wire.level_one_sector_explanation, confidence),
        "main_business_summary": {
            **_generic(wire.main_business_summary, confidence),
            "source_status": "VERIFIED_STRUCTURED" if verified_main else "LLM_UNVERIFIED",
        },
        "industry_position": {
            "level": wire.industry_position,
            "description": wire.industry_position_description,
            "source_status": "LLM_UNVERIFIED",
            "confidence": min(confidence, 0.3),
            "limitations": list(wire.missing_fields),
        },
        "concept_tags": [{"name": item, "source_status": "LLM_UNVERIFIED"} for item in wire.concept_tags],
        "core_products": list(wire.core_products) or ["信息不足*"],
        "main_theme": {
            "theme": None, "strength": 0, "core_beneficiary": False,
            "source_status": "LLM_UNVERIFIED", "confidence": 0,
        },
        "competitive_advantage": _generic(wire.competitive_advantage, confidence),
        "industry_trend": _generic(wire.industry_trend, confidence),
        "investment_logic": _generic(wire.investment_logic, confidence),
        "invalidation_conditions": list(wire.invalidation_conditions),
        "domestic_substitution": {
            "level": wire.domestic_substitution_level,
            "target_product": wire.domestic_substitution_target,
            "commercialization_stage": None,
            "source_status": "LLM_UNVERIFIED",
            "confidence": min(confidence, 0.25),
        },
        "observation_rating": wire.observation_rating,
        "financial_status": context.get("financial_status") or {},
        "data_conflict": wire.data_conflict,
        "missing_fields": missing,
        "requires_manual_review": True,
        "display_marker": "*",
        "current_market_main_theme": "UNKNOWN",
        "latest_industry_event": "UNKNOWN",
        "latest_company_event": "UNKNOWN",
        "current_policy_catalyst": "UNKNOWN",
        "current_news_catalyst": "UNKNOWN",
        "current_market_sentiment_from_news": "UNKNOWN",
    }


def _generic(summary: str, confidence: float) -> dict[str, Any]:
    return {"summary": summary, "source_status": "LLM_UNVERIFIED", "confidence": confidence}
