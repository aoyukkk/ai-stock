from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fundamentals.financial_status import FinancialStatusEngine
from fundamentals.normalizer import normalize_financial_snapshot
from fundamentals.schemas import ProvenancedField, SourceStatus


class TushareFundamentalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str
    stock_name: str | None = None
    as_of_time: datetime
    company_profile: dict[str, Any] = Field(default_factory=dict)
    level_one_sector: str | None = None
    industry_taxonomy: str = "UNKNOWN"
    main_business: list[dict[str, Any]] = Field(default_factory=list)
    core_products: list[str] = Field(default_factory=list)
    main_business_breakdown: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    source_concept_tags: list[str] = Field(default_factory=list)
    normalized_concept_tags: list[str] = Field(default_factory=list)
    concept_source_status: str = "UNKNOWN"
    concept_mapping_audit: dict[str, Any] = Field(default_factory=dict)
    financial_summary: dict[str, Any] = Field(default_factory=dict)
    financial_status: dict[str, Any]
    valuation_summary: dict[str, Any] = Field(default_factory=dict)
    shareholder_risk_summary: dict[str, Any] = Field(default_factory=dict)
    data_coverage: dict[str, bool] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    field_provenance_map: dict[str, ProvenancedField]
    available_at: datetime | None = None
    profile_version: str
    latest_financial_period: str | None = None
    report_type: str | None = None
    announcement_date: str | None = None
    financial_data_age_days: int | None = None
    is_latest_available_as_of_decision_time: bool = False
    stale_reason: str | None = None


class TushareFundamentalProfileBuilder:
    def build(
        self,
        stock_code: str,
        *,
        stock_basic: dict[str, Any] | None = None,
        company: dict[str, Any] | None = None,
        income: dict[str, Any] | None = None,
        balance: dict[str, Any] | None = None,
        cashflow: dict[str, Any] | None = None,
        indicator: dict[str, Any] | None = None,
        daily_basic: dict[str, Any] | None = None,
        main_business: list[dict[str, Any]] | None = None,
        risks: dict[str, Any] | None = None,
        concept_tags: list[str] | None = None,
        concept_mapping_audit: dict[str, Any] | None = None,
        as_of_time: datetime | None = None,
        financial_selection: dict[str, Any] | None = None,
    ) -> TushareFundamentalProfile:
        now = as_of_time or datetime.now(timezone.utc)
        financial_selection = financial_selection or {}
        basic, company = stock_basic or {}, company or {}
        financial = normalize_financial_snapshot(
            income or {}, balance or {}, cashflow or {}, indicator or {}, daily_basic or {}
        )
        industry = basic.get("industry")
        taxonomy = "Tushare industry" if industry else "UNKNOWN"
        status = FinancialStatusEngine().evaluate(
            financial,
            structured_industry=industry,
            classification_source=taxonomy,
        )
        business_rows = list(main_business or [])
        concept_tags = list(concept_tags or [])
        concept_mapping_audit = dict(concept_mapping_audit or {})
        if not business_rows:
            structured_main_business = (
                company.get("main_business")
                or company.get("business_scope")
                or company.get("introduction")
            )
            if structured_main_business:
                business_rows = [{
                    "ts_code": stock_code,
                    "bz_item": str(structured_main_business),
                    "business_type": "P",
                    "source_interface": "stock_company",
                    "source_field": (
                        "main_business" if company.get("main_business")
                        else "business_scope" if company.get("business_scope")
                        else "introduction"
                    ),
                }]
        breakdown = {
            kind: [row for row in business_rows if str(row.get("business_type") or row.get("type") or "") == kind]
            for kind in ("P", "I", "D")
        }
        core_products = [str(row.get("bz_item")) for row in breakdown["P"] if row.get("bz_item")][:20]
        available_text = financial.get("available_at")
        available_at = datetime.fromisoformat(available_text) if isinstance(available_text, str) else None
        provenance = {
            "company_profile": _structured(company, list(company)),
            "level_one_sector": _structured(industry, ["stock_basic.industry"]) if industry else _unknown(),
            "main_business": _structured(
                business_rows,
                sorted({
                    "stock_company." + str(row.get("source_field"))
                    if row.get("source_interface") == "stock_company"
                    else "fina_mainbz_vip"
                    for row in business_rows
                }),
            ),
            "concept_tags": _structured(concept_tags, ["ths_index", "ths_member"]) if concept_tags else _unknown(),
            "financial_summary": _structured(financial, list(financial)),
            "financial_status": ProvenancedField(
                value=status.status,
                source_status=SourceStatus.DERIVED_RULE,
                verified=True,
                confidence=0.8 if status.status != "INSUFFICIENT_DATA" else 0.2,
                evidence_fields=status.evidence_fields,
            ),
        }
        missing = [key for key, item in provenance.items() if item.source_status is SourceStatus.UNKNOWN]
        material = json.dumps(
            {
                "stock_code": stock_code, "available_at": available_text, "financial": financial,
                "main_business": business_rows, "company": company, "concept_tags": concept_tags,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        version = "tushare-profile-v1-" + hashlib.sha256(material.encode()).hexdigest()[:12]
        return TushareFundamentalProfile(
            stock_code=stock_code,
            stock_name=basic.get("name"),
            as_of_time=now,
            company_profile=company,
            level_one_sector=industry,
            industry_taxonomy=taxonomy,
            main_business=business_rows,
            core_products=core_products,
            main_business_breakdown=breakdown,
            source_concept_tags=concept_tags,
            normalized_concept_tags=concept_tags,
            concept_source_status="VERIFIED_STRUCTURED" if concept_tags else "UNKNOWN",
            concept_mapping_audit=concept_mapping_audit,
            financial_summary=financial,
            financial_status=status.model_dump(mode="json"),
            valuation_summary={key: financial.get(key) for key in ("pe", "pb", "ps", "total_market_value", "circulating_market_value", "turnover_rate")},
            shareholder_risk_summary=risks or {},
            data_coverage={key: value.source_status is not SourceStatus.UNKNOWN for key, value in provenance.items()},
            missing_fields=missing,
            field_provenance_map=provenance,
            available_at=available_at,
            profile_version=version,
            latest_financial_period=financial_selection.get("latest_financial_period") or financial.get("end_date"),
            report_type=financial_selection.get("report_type") or financial.get("report_type"),
            announcement_date=financial_selection.get("announcement_date") or financial.get("f_ann_date") or financial.get("ann_date"),
            financial_data_age_days=financial_selection.get("data_age_days"),
            is_latest_available_as_of_decision_time=bool(financial_selection.get("is_latest_available_as_of_decision_time", available_at is not None)),
            stale_reason=financial_selection.get("stale_reason"),
        )


def _structured(value: Any, evidence: list[str]) -> ProvenancedField:
    if value in (None, "", [], {}):
        return _unknown()
    return ProvenancedField(
        value=value,
        source_status=SourceStatus.VERIFIED_STRUCTURED,
        verified=True,
        confidence=1,
        evidence_fields=evidence,
    )


def _unknown() -> ProvenancedField:
    return ProvenancedField(value=None, source_status=SourceStatus.UNKNOWN, verified=False, confidence=0)
