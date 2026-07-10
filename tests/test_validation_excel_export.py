from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from scripts.run_guarded_llm_excel_validation import SHEET_NAMES, _build_excel, _validate_xlsx


def _payload():
    samples = []
    plans = []
    allocations = []
    audits = []
    for rank, code in ((1, "000001"), (250, "000002"), (500, "688001")):
        fundamental = {
            "industry_chain": {"chain_name": "UNKNOWN", "source_status": "LLM_UNVERIFIED", "display_marker": "*", "confidence": 0.2},
            "level_one_sector_explanation": {"summary": "测试", "source_status": "LLM_UNVERIFIED", "display_marker": "*"},
            "main_business_summary": {"summary": "结构化主营摘要", "source_status": "VERIFIED_STRUCTURED", "derivation_status": "LLM_SUMMARY", "display_marker": ""},
            "industry_position": {"description": "UNKNOWN", "source_status": "LLM_UNVERIFIED", "display_marker": "*"},
            "concept_tags": [], "structural_theme_fit": {"value": "UNKNOWN", "source_status": "LLM_UNVERIFIED", "display_marker": "*"},
            "competitive_advantage": {"summary": "UNKNOWN", "source_status": "LLM_UNVERIFIED", "display_marker": "*"},
            "industry_trend": {"summary": "UNKNOWN", "source_status": "LLM_UNVERIFIED", "display_marker": "*"},
            "investment_logic": {"summary": "UNKNOWN", "source_status": "LLM_UNVERIFIED", "display_marker": "*"},
            "domestic_substitution": {"level": "INSUFFICIENT_DATA", "source_status": "LLM_UNVERIFIED", "display_marker": "*"},
            "observation_rating": "INSUFFICIENT_DATA", "financial_status": {"status": "INSUFFICIENT_DATA"},
            "current_market_main_theme": "UNKNOWN", "latest_industry_event": "UNKNOWN", "latest_company_event": "UNKNOWN",
            "current_policy_catalyst": "UNKNOWN", "current_news_catalyst": "UNKNOWN", "current_market_sentiment_from_news": "UNKNOWN",
        }
        samples.append({
            "rank": rank, "stock_code": code, "stock_name": f"样本{rank}",
            "quant_scores": {key: 50 for key in ("total_score","technical_score","capital_score","emotion_score","momentum_score","risk_score")},
            "profile_version": f"profile-{rank}", "latest_financial_period": "20260331",
            "financial_available_at": "2026-04-30T00:00:00+08:00", "data_age_days": 70,
            "quant_run_id": "quant-test", "run_data_manifest_id": "manifest-test",
            "fundamental_result": fundamental,
            "screening_result": {"screening_decision": "WATCH_ONLY", "llm_score": 0, "confidence": 0},
            "field_provenance": {"main_business": {"value": "测试", "source_status": "VERIFIED_STRUCTURED", "verified": True, "confidence": 1, "evidence_fields": ["mainbz"]}},
            "missing_fields": ["news"],
        })
        plans.append({
            "id": rank, "stock_code": code, "plan_purpose": "MODEL_VALIDATION", "plan_session": "POST_MARKET",
            "status": "DRAFT", "actionable": False, "is_final_recommendation": False,
            "conservative_price": 9.8, "balanced_price": 10, "aggressive_price": 10.2, "recommended_price": 10,
            "max_acceptable_price": 10.5, "stop_loss_price": 9.5, "take_profit_1_price": 10.5, "take_profit_2_price": 11,
            "fill_probability": .5, "risk_reward": 1.5, "order_price_score": 50, "support": 9.7, "resistance": 10.1,
            "atr": .5, "vwap": 10, "previous_close": 10, "limit_up_estimated": 11, "limit_down_estimated": 9,
            "limit_price_source": "RULE_ESTIMATED", "official_target_day_limit_available": False, "target_day_auction_available": False,
            "cancel_conditions": {}, "reprice_conditions": {}, "warnings": ["NON_ACTIONABLE"], "temporal_status": "PASS",
            "created_at": "2026-07-10T00:00:00Z",
        })
        allocations.append({
            "allocation_run_id": "allocation-test", "stock_code": code, "allocation_purpose": "MODEL_VALIDATION",
            "status": "NON_ACTIONABLE", "actionable": False, "relative_allocation_weight": .3333,
            "suggested_position_percent": .1, "suggested_capital_amount": 100000, "suggested_quantity": 10000,
            "estimated_max_loss": 5000, "binding_constraints": ["risk"], "warnings": ["NON_ACTIONABLE"],
            "account_snapshot_id": "snapshot-test", "created_at": "2026-07-10T00:00:00Z",
        })
        for task in ("fundamental_structured_inference", "structured_light_screening"):
            audits.append({"stock_code": code, "task": task, "knowledge_mode": "STRUCTURED_INPUT_ONLY", "model_alias": "light-screening-default", "actual_model": "deepseek-v4-flash", "prompt_version": "test-v1", "status": "ok", "schema_status": "PASS", "request_hash": hashlib.sha256(f"{code}{task}".encode()).hexdigest(), "input_tokens": 1, "output_tokens": 1, "cost_usd": 0, "latency_ms": 1, "cache_status": "MISS"})
    run = {"run_id":"validation-test","quant_run_id":"quant-test","run_data_manifest_id":"manifest-test","status":"COMPLETED","knowledge_mode":"STRUCTURED_INPUT_ONLY","decision_time":"2026-07-09T20:00:00+08:00","target_trade_date":"2026-07-10","real_llm":True,"created_at":"2026-07-10T00:00:00Z","expected_universe_audit":{}}
    return {"run":run,"samples":samples,"audits":audits,"plans":plans,"snapshots":[],"allocations":allocations,"dynamic_fields_unknown":True,"secret_scan":"PASS","content_hash":"fixture-sha256"}


def test_validation_excel_export_has_13_sheets_formulas_and_validations(tmp_path: Path):
    payload = _payload()
    output = tmp_path / "validation.tmp.xlsx"
    _build_excel(payload, output, tmp_path)
    result = _validate_xlsx(output, payload)
    assert result["status"] == "PASS"
    with zipfile.ZipFile(output) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        assert all(name in workbook_xml for name in SHEET_NAMES)
        xml = "\n".join(archive.read(name).decode("utf-8", errors="ignore") for name in archive.namelist() if name.endswith(".xml"))
        assert "dataValidation" in xml
        assert "<x:f>" in xml or "<f>" in xml
        assert "reasoning_content" not in xml
