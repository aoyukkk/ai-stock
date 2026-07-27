from __future__ import annotations

import json
import re
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.quant_run import QuantRun
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationFailureAudit,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
)
from database.session import create_engine_from_url, get_session
from research.structured_validation import StructuredOutputValidationError
from scripts.run_trader_demo_excel import (
    _audit_scope_label,
    _safe_audit,
    SHEET_NAMES,
    _build_excel,
    _read_manual_file,
    _secret_scan,
    _sidecar,
    _validate_payload,
    _validate_xlsx,
)
from trader_demo.service import ManualSelection, TraderDemoService


def _payload() -> dict:
    quant_rows = []
    llm_rows = []
    order_rows = []
    fundamental_rows = []
    decisions = [
        ("000001", "ADVANCE", True, False, "LLM", "SUCCESS"),
        ("000002", "REJECT", False, True, "MANUAL", "SUCCESS"),
        ("000003", "SCHEMA_ERROR", False, True, "MANUAL", "FAILED"),
        ("000004", "ADVANCE", True, True, "BOTH", "SUCCESS"),
    ]
    for rank, (code, decision, llm_selected, manual_selected, source, execution) in enumerate(decisions, 1):
        quant_rows.append({
            "rank": rank, "stock_code": code, "stock_name": f"样本{rank}", "exchange": "SZ",
            "level_one_sector": "测试行业", "classification_standard": "TUSHARE_STOCK_BASIC_INDUSTRY",
            "total_score": 80-rank, "technical_score": 70, "capital_score": 60, "emotion_score": 50,
            "momentum_score": 75, "risk_score": 65, "limit_status": "NORMAL", "data_coverage_status": "COMPLETE",
            "quant_top500": True, "llm_evaluated": True, "llm_selected": llm_selected,
            "manual_selected": manual_selected, "selection_source": source, "quant_run_id": "quant-test",
        })
        llm_rows.append({
            "sequence": rank, "stock_code": code, "stock_name": f"样本{rank}", "quant_rank": rank,
            "quant_score": 80-rank, "llm_score": None if execution == "FAILED" else 70-rank,
            "decision": decision, "confidence": 0 if execution == "FAILED" else 0.7,
            "observation_rating": "KEY_WATCH", "financial_status": "NORMAL", "data_quality_score": 0.9,
            "data_conflict": False, "llm_selected": llm_selected, "manual_selected": manual_selected,
            "trading_candidate": True, "selection_source": source, "reason": "结构化判断",
            "risk_note": "需人工复核", "missing_information": "新闻", "execution_status": execution,
            "error_category": "SCHEMA_ERROR" if execution == "FAILED" else "",
        })
        order_rows.append({
            "stock_code": code, "stock_name": f"样本{rank}", "selection_source": source,
            "manual_reason": "人工关注" if manual_selected else "", "quant_rank": rank, "quant_score": 80-rank,
            "llm_score": None if execution == "FAILED" else 70-rank, "llm_decision": decision,
            "observation_rating": "KEY_WATCH", "risk_level": "HIGH" if decision != "ADVANCE" else "NORMAL",
            "conservative_price": 9.8, "balanced_price": 10, "aggressive_price": 10.2,
            "recommended_price": None if decision != "ADVANCE" else 10, "max_acceptable_price": 10.5,
            "stop_loss_price": None if decision != "ADVANCE" else 9.5, "take_profit_1": 10.5,
            "take_profit_2": 11, "risk_reward": 1.5, "fill_probability": 0.5,
            "order_status": "BLOCKED" if decision != "ADVANCE" else "DRAFT",
            "order_warning": "人工选择，但LLM未入选" if decision != "ADVANCE" else "仅供模型验证",
            "relative_weight": 0 if decision != "ADVANCE" else 0.5,
            "position_percent": 0 if decision != "ADVANCE" else 0.05,
            "capital_amount": 0 if decision != "ADVANCE" else 50000, "quantity": 0 if decision != "ADVANCE" else 5000,
            "max_loss": 0 if decision != "ADVANCE" else 2500, "risk_budget": 0 if decision != "ADVANCE" else 2500,
            "position_status": "NON_ACTIONABLE", "binding_constraint": "risk", "position_warning": "仅供模型验证",
            "level_one_sector": "测试行业", "chain_position": "MIDSTREAM_COMPONENT*",
            "main_business": "主营摘要", "financial_status": "NORMAL", "actionable": "否",
        })
        fundamental_rows.append({
            "stock_code": code, "stock_name": f"样本{rank}", "selection_source": source,
            "quant_rank": rank, "llm_score": None if execution == "FAILED" else 70-rank,
            "industry_chain": "测试产业链*", "chain_position": "MIDSTREAM_COMPONENT*", "level_one_sector": "测试行业",
            "classification_standard": "TUSHARE_STOCK_BASIC_INDUSTRY", "main_business": "结构化主营摘要",
            "core_products": "产品甲；产品乙", "industry_position": "参与者*", "concept_tags": "概念甲；概念乙",
            "structural_theme_fit": "信息不足*", "competitive_advantage": "信息不足*", "industry_trend": "信息不足*",
            "investment_logic": "信息不足*", "logic_invalidation": "信息不足", "domestic_substitution": "INSUFFICIENT_DATA*",
            "observation_rating": "KEY_WATCH", "financial_status": "NORMAL", "financial_status_reason": "规则判断",
            "financial_period": "20260331", "revenue": 10000, "revenue_yoy": 0.1, "net_profit": 1000,
            "net_profit_yoy": 0.05, "deducted_net_profit": 900, "deducted_profit_yoy": 0.04,
            "gross_margin": 0.3, "net_margin": 0.1, "operating_cash_flow": 800, "debt_ratio": 0.25,
            "cash": 2000, "trading_financial_assets": 50, "total_assets": 20000, "total_liabilities": 5000,
            "unverified_fields": "industry_chain；industry_position", "missing_fields": "新闻",
            "data_conflict": "否", "fundamental_confidence": 0.25, "manual_review": "是",
        })
    audits = []
    for code, _, _, _, _, execution in decisions:
        for task in ("fundamental_structured_inference", "structured_light_screening"):
            failed = code == "000003" and task == "structured_light_screening"
            audits.append({
                "stock_code": code, "task": task, "knowledge_mode": "STRUCTURED_INPUT_ONLY",
                "model_alias": "light-screening-default", "actual_model": "deepseek-v4-flash",
                "prompt_version": "test-v2", "status": "schema_error" if failed else "ok",
                "schema_status": "SCHEMA_ERROR" if failed else "PASS", "request_hash": f"hash-{code}-{task}",
                "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001, "latency_ms": 100,
                "cache_status": "MISS", "error_category": "SCHEMA_ERROR" if failed else "",
                "error_field": "$.missing_data" if failed else "", "error_message": "must be an array" if failed else "",
            })
    return {
        "generated_at": "2026-07-10T07:00:00+00:00",
        "run": {"run_id":"trader-demo-test","quant_run_id":"quant-test","run_data_manifest_id":"manifest-test","base_market_trade_date":"2026-07-09","target_trade_date":"2026-07-10","knowledge_mode":"STRUCTURED_INPUT_ONLY","status":"PARTIAL_SUCCESS","expected_universe_audit":{}},
        "quant_rows": quant_rows, "llm_rows": llm_rows, "order_rows": order_rows,
        "fundamental_rows": fundamental_rows,
        "errors": [{"stock_code":"000003","module":"structured_light_screening","status":"SCHEMA_ERROR","reason":"$.missing_data: must be an array","affects_selection":"是","affects_order":"是","affects_position":"是"}],
        "warnings": [{"stock_code":"000004","module":"order_plan","status":"BLOCKED_WARNING","reason":"risk gate","affects_selection":"否","affects_order":"是","affects_position":"是"}],
        "audits": audits, "account": {"account_equity":1000000,"available_cash":1000000},
        "row_counts": {"quant_scored_count":4,"quant_sheet_rows":4,"llm_evaluation_count":4,"llm_sheet_rows":4,"llm_selected_count":2,"manual_selected_count":3,"trading_candidate_count":4,"order_sheet_rows":4,"fundamental_sheet_rows":4},
        "content_hash": "fixture-sha256",
    }


def test_trader_demo_workbook_has_five_sheets_active_plan_and_auditable_formulas(tmp_path: Path):
    payload = _payload()
    output = tmp_path / "trader-demo.xlsx"
    _validate_payload(payload)
    _build_excel(payload, output, tmp_path)
    result = _validate_xlsx(output, payload)
    assert result["sheet_count"] == 5
    with zipfile.ZipFile(output) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        assert 'activeTab="2"' in workbook_xml
        assert len(re.findall(r"<(?:\w+:)?sheet\b", workbook_xml)) == 5
        xml = "\n".join(archive.read(name).decode("utf-8", errors="ignore") for name in archive.namelist() if name.endswith(".xml"))
        sheet1 = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert re.search(r'<x:c[^>]*r="B2"[^>]*t="inlineStr".*?<x:t>000001</x:t>', sheet1)
        assert "reasoning_content" not in xml
        assert "authorization: bearer" not in xml.lower()
        assert "{\"" not in xml
        assert "conditionalFormatting" in xml
        assert "autoFilter" in xml
        assert "<x:f>" in xml or "<f>" in xml
        assert "测试产业链*" in xml


def test_payload_pool_invariants_and_partial_failure():
    payload = _payload()
    _validate_payload(payload)
    assert {row["stock_code"] for row in payload["order_rows"]} == {row["stock_code"] for row in payload["fundamental_rows"]}
    assert not next(row for row in payload["llm_rows"] if row["stock_code"] == "000003")["llm_selected"]
    assert next(row for row in payload["llm_rows"] if row["stock_code"] == "000002")["trading_candidate"]
    assert next(row for row in payload["llm_rows"] if row["stock_code"] == "000004")["selection_source"] == "BOTH"
    assert payload["run"]["status"] == "PARTIAL_SUCCESS"


def test_manual_csv_and_pool_deduplication(tmp_path: Path):
    csv_path = tmp_path / "manual.csv"
    csv_path.write_text("stock_code,manual_reason,priority\n000001.SZ,测试,A\n", encoding="utf-8")
    manual = _read_manual_file(csv_path)
    assert manual == [ManualSelection("000001.SZ", "测试", "A")]
    rows = [SimpleNamespace(rank=1, stock_code="000001"), SimpleNamespace(rank=2, stock_code="000002")]
    selected = TraderDemoService.evaluation_rows(rows, ranks=(1,), top_n=None, manual=manual)
    assert [row.stock_code for row in selected] == ["000001"]
    with pytest.raises(ValueError, match="MANUAL_STOCK_NOT_IN_QUANT_RUN"):
        TraderDemoService.evaluation_rows(rows, ranks=None, top_n=1, manual=[ManualSelection("600000")])
    forced = TraderDemoService.evaluation_rows(
        rows,
        ranks=None,
        top_n=1,
        manual=[ManualSelection("600730.SH", "人工关注", "HIGH")],
        allow_manual_outside_quant=True,
    )
    assert [row.stock_code for row in forced] == ["000001", "600730.SH"]
    assert forced[-1].manual_review_only is True
    assert forced[-1].total_score is None


def test_manual_st_review_is_deep_review_candidate_but_order_stays_blocked():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        service = TraderDemoService(session)
        run = QuantRun(
            run_id="quant-st-review",
            request_hash="quant-st-review-hash",
            run_mode="POST_MARKET_FINAL",
            decision_time=datetime(2026, 7, 24, 7, tzinfo=timezone.utc),
            base_market_trade_date=date(2026, 7, 24),
            target_trade_date=date(2026, 7, 27),
            factor_version="TUSHARE_BASELINE_V1",
            config_snapshot={},
            data_manifest_id="manifest-st-review",
            no_llm_call_verified=True,
            temporal_status="PASS",
            actionable=True,
            status="COMPLETED",
        )
        validation = ModelValidationRun(
            run_id="flash-st-review",
            quant_run_id=run.run_id,
            run_data_manifest_id=run.data_manifest_id,
            run_mode=run.run_mode,
            knowledge_mode="STRUCTURED_INPUT_ONLY",
            decision_time=run.decision_time,
            base_market_trade_date=run.base_market_trade_date,
            target_trade_date=run.target_trade_date,
            real_llm=True,
            status="SUCCESS",
            request_hash="flash-st-review-hash",
            config_snapshot={},
            expected_universe_audit={},
            warnings=[],
        )
        sample = ModelValidationSample(
            validation_run_id=validation.run_id,
            quant_run_id=run.run_id,
            run_data_manifest_id=run.data_manifest_id,
            rank=5313,
            stock_code="600730.SH",
            stock_name="*ST高科",
            quant_scores={
                "total_score": None,
                "technical_score": None,
                "capital_score": None,
                "emotion_score": None,
                "momentum_score": None,
                "risk_score": None,
            },
            profile_version="test-st-v1",
            selected_at=run.decision_time,
            fundamental_result={},
            screening_result={
                "screening_decision": "WATCH_ONLY",
                "confidence": 0.5,
                "_trader_demo": {
                    "execution_status": "SUCCESS",
                    "selection_source": "MANUAL",
                    "manual_review_only": True,
                    "hard_gate_review_only": True,
                    "hard_gate_reasons": [
                        "MANUAL_OUTSIDE_ACTIONABLE_QUANT_UNIVERSE",
                        "ST",
                    ],
                    "order_eligible": False,
                },
            },
            field_provenance={},
            missing_fields=[],
        )
        session.add_all([run, validation, sample])
        session.commit()
        service.guard._profile = lambda *_args: SimpleNamespace(
            stock_name="*ST高科"
        )

        service.generate_candidate_outputs(
            validation.run_id,
            account_equity=Decimal("100000"),
            available_cash=Decimal("100000"),
        )

        plan = session.scalar(select(ModelValidationOrderPlan))
        allocation = session.scalar(select(ModelValidationAllocation))
        assert plan is not None
        assert plan.status == "BLOCKED"
        assert plan.recommended_price is None
        assert any(
            "MANUAL_REVIEW_ONLY_HARD_GATE" in warning
            for warning in plan.warnings
        )
        assert allocation is not None
        assert allocation.suggested_position_percent == 0
        assert allocation.suggested_quantity == 0
    finally:
        session.close()
        engine.dispose()


def test_sidecar_contains_compact_audit_without_secret(tmp_path: Path):
    payload = _payload()
    sidecar = _sidecar(payload, {"status":"PASS"}, tmp_path / "demo.xlsx", "abc")
    text = json.dumps(sidecar, ensure_ascii=False)
    assert _secret_scan(text)
    assert "reasoning_content" not in text
    assert "must be an array" in text


def test_empty_candidate_pool_has_no_candidate_fundamentals_and_shows_honest_note(tmp_path: Path):
    payload = _payload()
    for row in payload["llm_rows"]:
        row.update({"llm_selected": False, "manual_selected": False, "trading_candidate": False, "selection_source": ""})
    payload["order_rows"] = []
    payload["fundamental_rows"] = []
    payload["row_counts"].update({
        "llm_selected_count": 0, "manual_selected_count": 0,
        "trading_candidate_count": 0, "order_sheet_rows": 0, "fundamental_sheet_rows": 0,
    })
    _validate_payload(payload)
    output = tmp_path / "empty-candidates.xlsx"
    _build_excel(payload, output, tmp_path)
    with zipfile.ZipFile(output) as archive:
        xml = "\n".join(archive.read(name).decode("utf-8", errors="ignore") for name in archive.namelist() if name.endswith(".xml"))
    assert "本次无交易候选：LLM入选0只，人工选择0只。" in xml
    assert payload["row_counts"]["fundamental_sheet_rows"] == 0


def test_failure_audit_uses_independent_minimal_transaction():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        service = TraderDemoService(session)
        exc = StructuredOutputValidationError(
            stock_code="001390.SZ", task="fundamental_structured_inference",
            prompt_version="test-v3", field="$.main_business_summary",
            category="SCHEMA_ERROR", detail="string_type",
        )
        service._persist_failure_independently("run-test", exc, {
            "diagnostics": {"finish_reason": "stop", "content_length": 10, "response_sha256": "a" * 64}
        })
        row = session.scalar(select(ModelValidationFailureAudit))
        assert row is not None
        assert row.stock_code == "001390.SZ"
        assert row.diagnostics["content_length"] == 10
        assert "content" not in row.diagnostics
    finally:
        session.close()
        engine.dispose()


def test_audit_scope_label_preserves_non_stock_portfolio_scope():
    assert _audit_scope_label("000001.SZ") == "000001"
    assert _audit_scope_label("PORTFOLIO") == "PORTFOLIO"
    audit = _safe_audit({
        "diagnostics": {
            "reasoning_content_stored": False,
            "repair_response": "must not be exported",
            "finish_reason": "stop",
        }
    })
    assert audit["diagnostics"] == {"finish_reason": "stop", "reasoning_stored": False}
    assert "reasoning_content" not in json.dumps(audit)
