from __future__ import annotations

import json
from typing import Any

from stock_codes import normalize_ts_code


def summarize_structured_input(context: dict[str, Any]) -> dict[str, Any]:
    company = context.get("company_profile") or {}
    breakdown = context.get("main_business_breakdown") or {}
    financial = context.get("financial_summary") or {}
    main_rows = context.get("main_business") or []
    present = {
        "company_profile": bool(company),
        "main_business": bool(main_rows),
        "business_scope": bool(company.get("business_scope")),
        "industry": bool(context.get("level_one_sector")),
        "financial_summary": bool(financial),
    }
    missing_required = [key for key in ("company_profile", "main_business", "industry", "financial_summary") if not present[key]]
    text = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    canonical = normalize_ts_code(str(context.get("stock_code") or ""))
    quant = context.get("quant") or {}
    status = "PASS" if not missing_required else "INPUT_PROFILE_INCOMPLETE"
    return {
        "stock_code": canonical.split(".", 1)[0],
        "canonical_ts_code": canonical,
        "status": status,
        "present": present,
        "company_profile_present": present["company_profile"],
        "main_business_present": present["main_business"],
        "business_scope_present": present["business_scope"],
        "structured_industry_present": present["industry"],
        "main_business_breakdown_rows": sum(len(value) for value in breakdown.values() if isinstance(value, list)),
        "main_business_breakdown_count": sum(len(value) for value in breakdown.values() if isinstance(value, list)),
        "concept_tag_count": len(context.get("concept_tags") or []),
        "source_concept_tag_count": len(context.get("concept_tags") or []),
        "financial_field_count": sum(1 for value in financial.values() if value not in (None, "", [], {})),
        "financial_period": financial.get("end_date"),
        "financial_status": (context.get("financial_status") or {}).get("status"),
        "quant_factor_count": sum(1 for value in quant.values() if value not in (None, "", [], {})),
        "missing_required": missing_required,
        "missing_required_fields": missing_required,
        "declared_missing_fields": list(context.get("missing_fields") or []),
        "character_count": len(text),
        "input_character_count": len(text),
        "estimated_input_tokens": max(1, len(text) // 3),
    }
