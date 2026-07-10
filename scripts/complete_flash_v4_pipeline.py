from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from database.models.system import LLMUsage
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationLLMAudit,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
    ProResumeRun,
)
from database.session import get_session, init_db
from scripts.run_guarded_llm_excel_validation import _secret_scan
from scripts.run_trader_demo_excel import _build_excel, _serialize_readback, _sidecar, _validate_xlsx
from stock_codes import normalize_ts_code
from trader_demo.pro_single_v3 import (
    PORTFOLIO_CONTRACT_VERSION,
    PORTFOLIO_PROMPT_VERSION,
    RANKING_VERSION,
    SINGLE_CONTRACT_VERSION,
    SINGLE_PROMPT_VERSION,
    ProSingleV3Service,
    ensure_v3_schema,
    select_v3_canary,
    stable_v3_order,
)
from trader_demo.resume_checkpoint import load_resume_context
from trader_demo.runtime import temporary_real_llm_runtime
from trader_demo.service import TraderDemoService
from trader_demo.usage_ledger import AuthoritativeUsageLedger, PIPELINE_RUN_ID


FLASH_RUN_ID = "trader-demo-ca9eee27004b4fcda754"
RETRY_RUN_ID = "trader-demo-71a1f64d1c514683a94b"
CANARY_RUN_ID = "trader-demo-be5ca13551c74fba87e5"
DAILY_OUTPUT = ROOT / "outputs" / "2026-07-10"
CHECKPOINT = DAILY_OUTPUT / "审计" / "flash_v4_checkpoint.json"
REPORT = ROOT / "data" / "reports" / "flash_v4_final_pipeline_20260710_report.json"


def main() -> int:
    init_db()
    session = get_session()
    try:
        ensure_v3_schema(session.get_bind())
        resolved = _merge_retry_successes(session)
        context = load_resume_context(session, FLASH_RUN_ID)
        ordered = stable_v3_order(context.candidates)
        resume = _create_pro_run(session, context, ordered)
        ledger = AuthoritativeUsageLedger(session.get_bind())
        pro_usage_before = _pro_usage(session, resume.run_id)
        with temporary_real_llm_runtime():
            service = ProSingleV3Service(session, ledger)
            canary_reports = service.run_canary(
                resume, select_v3_canary(ordered), context.selection_sources, CHECKPOINT
            )
            review_reports = service.run_reviews(
                resume, ordered, context.selection_sources, CHECKPOINT,
                stage="FULL", stop_on_failure=True,
            )
            reviews = service.rank_and_apply(resume, ordered, context.selection_sources)
            portfolio, portfolio_report = service.run_portfolio(
                resume, ordered, reviews, context.selection_sources
            )
        if _runtime_switches() != {
            "LLM_REAL_CALLS_ENABLED": "false",
            "RUN_REAL_FUNDAMENTAL_RESEARCH": "false",
            "LLM_GATEWAY_MOCK_ONLY": "true",
        }:
            raise RuntimeError("REAL_LLM_RUNTIME_SWITCH_RESTORE_FAILED")

        trader = TraderDemoService(session)
        if session.scalar(select(ModelValidationOrderPlan.id).where(
            ModelValidationOrderPlan.validation_run_id == FLASH_RUN_ID
        )) is None:
            trader.generate_candidate_outputs(
                FLASH_RUN_ID,
                account_equity=Decimal("1000000"), available_cash=Decimal("1000000"),
            )
        payload = _serialize_readback(trader.readback(FLASH_RUN_ID))
        if len(payload["order_rows"]) != len(context.candidates):
            raise ValueError("FLASH_V4_CANDIDATE_OUTPUT_COUNT_MISMATCH")
        resolved_errors = _resolved_history(session)
        payload["resolved_historical_errors"] = resolved_errors
        v4_usage = _flash_v4_usage(session)
        pro_usage = _pro_usage(session, resume.run_id)
        old_known = 934_586
        total_known = old_known + v4_usage["actual_tokens"] + pro_usage["tokens"]
        payload["budget"] = {
            "limits": {"daily_limit": 5_000_000, "warning_threshold": 4_000_000, "final_reserve": 300_000},
            "usage": {
                "flash": v4_usage["actual_tokens"], "pro": pro_usage["tokens"],
                "repair": v4_usage["repair_tokens"], "connectivity": 288,
            },
            "total": total_known, "remaining": 5_000_000 - total_known,
            "warning_triggered": total_known >= 4_000_000,
        }
        payload["pro_resume"] = {
            "run_id": resume.run_id, "candidate_set_hash": context.candidate_set_hash,
            "portfolio_result": resume.portfolio_result,
            "token_ledger": {
                "current_resume_new_api_tokens": pro_usage["tokens"],
                "pipeline_total_actual_api_tokens": total_known,
                "remaining_budget_from_known_actual": 5_000_000 - total_known,
                "pro_v1_unavailable_count": 1 + v4_usage["unavailable_call_count"],
            },
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        payload["content_hash"] = hashlib.sha256(serialized.encode()).hexdigest()

        output_dir = DAILY_OUTPUT / "历史版本" / "完整流水线"
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / f"ai_trader_flash_v4_20260710_{resume.run_id[-8:]}.xlsx"
        if final_path.exists():
            raise FileExistsError(f"OUTPUT_EXISTS:{final_path.name}")
        temp_path = output_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp.xlsx"
        _build_excel(payload, temp_path, output_dir)
        validation = _validate_xlsx(temp_path, payload)
        os.replace(temp_path, final_path)
        workbook_hash = hashlib.sha256(final_path.read_bytes()).hexdigest()
        sidecar = _sidecar(payload, validation, final_path, workbook_hash)
        sidecar["current_errors"] = payload["errors"]
        sidecar["current_warnings"] = _current_warnings(payload)
        sidecar["resolved_historical_errors"] = resolved_errors
        sidecar["schema_errors"] = [
            row for row in sidecar["llm_usage"]
            if row.get("schema_status") not in {"PASS", "RESOLVED_HISTORY"}
        ]
        sidecar_text = json.dumps(sidecar, ensure_ascii=False, indent=2, default=str)
        if not _secret_scan(sidecar_text):
            raise ValueError("FLASH_V4_SIDECAR_SECRET_SCAN_FAILED")
        audit_path = final_path.with_name(final_path.stem + "_audit.json")
        audit_path.write_text(sidecar_text, encoding="utf-8")

        resume.status = "COMPLETED"
        session.commit()
        allocation_run_id = session.scalar(select(ModelValidationAllocation.allocation_run_id).where(
            ModelValidationAllocation.validation_run_id == FLASH_RUN_ID
        ).limit(1))
        checkpoint = {
            "stage": "COMPLETED", "final_status": "PARTIAL_SUCCESS",
            "quant_run_id": context.quant_run.run_id, "manifest_id": context.manifest.manifest_id,
            "flash_v4_run_id": FLASH_RUN_ID, "top20_v4_hash": context.top20_hash,
            "candidate_set_hash": context.candidate_set_hash, "pro_resume_run_id": resume.run_id,
            "order_run_id": f"{FLASH_RUN_ID}:order", "allocation_run_id": allocation_run_id,
            "excel_path": str(final_path), "workbook_sha256": workbook_hash,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "legacy_checkpoint": {"source": "daily_full_pipeline_checkpoint.json", "status": "ARCHIVED_REFERENCE"},
        }
        CHECKPOINT.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        report = _report(
            session, context, resume, payload, validation, final_path, audit_path,
            workbook_hash, resolved, canary_reports, review_reports, portfolio_report,
            v4_usage, pro_usage, pro_usage_before, total_known,
        )
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


def _merge_retry_successes(session) -> list[str]:
    retry_samples = {
        normalize_ts_code(row.stock_code): row
        for row in session.scalars(select(ModelValidationSample).where(
            ModelValidationSample.validation_run_id == RETRY_RUN_ID
        ))
    }
    passed = list(session.scalars(select(ModelValidationLLMAudit).where(
        ModelValidationLLMAudit.validation_run_id == RETRY_RUN_ID,
        ModelValidationLLMAudit.task == "fundamental_structured_inference",
        ModelValidationLLMAudit.schema_status == "PASS",
    )))
    resolved = []
    for audit in passed:
        code = normalize_ts_code(audit.stock_code)
        source = retry_samples[code]
        target = session.scalar(select(ModelValidationSample).where(
            ModelValidationSample.validation_run_id == FLASH_RUN_ID,
            ModelValidationSample.stock_code == code,
        ))
        if target is None:
            raise ValueError(f"FLASH_V4_BASE_SAMPLE_MISSING:{code}")
        target.fundamental_result = json.loads(json.dumps(source.fundamental_result, default=str))
        flag_modified(target, "fundamental_result")
        target_screening = json.loads(json.dumps(target.screening_result or {}, default=str))
        source_demo = (source.screening_result or {}).get("_trader_demo") or {}
        target_demo = target_screening.setdefault("_trader_demo", {})
        target_demo["execution_status"] = source_demo.get("execution_status") or "SUCCESS"
        target_demo["errors"] = list(source_demo.get("errors") or [])
        target.screening_result = target_screening
        flag_modified(target, "screening_result")
        old = session.scalar(select(ModelValidationLLMAudit).where(
            ModelValidationLLMAudit.validation_run_id == FLASH_RUN_ID,
            ModelValidationLLMAudit.stock_code == code,
            ModelValidationLLMAudit.task == "fundamental_structured_inference",
            ModelValidationLLMAudit.schema_status != "PASS",
        ).order_by(ModelValidationLLMAudit.id.desc()))
        if old is not None:
            old.schema_status = "RESOLVED_HISTORY"
            old.status = "RESOLVED"
        copied = session.scalar(select(ModelValidationLLMAudit.id).where(
            ModelValidationLLMAudit.validation_run_id == FLASH_RUN_ID,
            ModelValidationLLMAudit.stock_code == code,
            ModelValidationLLMAudit.task == audit.task,
            ModelValidationLLMAudit.schema_status == "PASS",
            ModelValidationLLMAudit.cache_status == "RETRY_RESOLVED",
        ))
        if copied is None:
            session.add(ModelValidationLLMAudit(
                validation_run_id=FLASH_RUN_ID, stock_code=code, task=audit.task,
                knowledge_mode=audit.knowledge_mode, model_alias=audit.model_alias,
                actual_model=audit.actual_model, prompt_version=audit.prompt_version,
                status=audit.status, schema_status="PASS", request_hash=audit.request_hash,
                input_tokens=audit.input_tokens, output_tokens=audit.output_tokens,
                cost_usd=audit.cost_usd, latency_ms=audit.latency_ms,
                cache_status="RETRY_RESOLVED", error_category=None, error_field=None,
                error_message=None, diagnostics={**dict(audit.diagnostics or {}), "retry_source_run_id": RETRY_RUN_ID},
            ))
        resolved.append(code)
    run = session.scalar(select(ModelValidationRun).where(ModelValidationRun.run_id == FLASH_RUN_ID))
    run.config_snapshot = {**dict(run.config_snapshot or {}), "resolved_retry_codes": sorted(resolved)}
    flag_modified(run, "config_snapshot")
    session.commit()
    return sorted(resolved)


def _create_pro_run(session, context, ordered) -> ProResumeRun:
    existing = session.scalar(select(ProResumeRun).where(
        ProResumeRun.flash_validation_run_id == FLASH_RUN_ID,
        ProResumeRun.candidate_set_hash == context.candidate_set_hash,
        ProResumeRun.pro_contract_version == SINGLE_CONTRACT_VERSION,
        ProResumeRun.status.in_(["RUNNING", "PRO_SUCCESS"]),
    ).order_by(ProResumeRun.id.desc()))
    if existing:
        return existing
    row = ProResumeRun(
        run_id=f"pro-resume-{uuid.uuid4().hex[:20]}", pipeline_run_id=PIPELINE_RUN_ID,
        quant_run_id=context.quant_run.run_id, manifest_id=context.manifest.manifest_id,
        flash_validation_run_id=FLASH_RUN_ID, previous_failed_run_id=None,
        pro_contract_version=SINGLE_CONTRACT_VERSION, prompt_version=SINGLE_PROMPT_VERSION,
        portfolio_prompt_version=PORTFOLIO_PROMPT_VERSION,
        base_trade_date=context.quant_run.base_market_trade_date,
        target_trade_date=context.quant_run.target_trade_date,
        top20_hash=context.top20_hash, manual_hash=context.manual_hash,
        candidate_set_hash=context.candidate_set_hash, candidate_count=len(ordered),
        chunk_size=1, chunk_count=len(ordered), status="RUNNING",
        config_snapshot={
            "candidate_mode": "SINGLE_STOCK", "concurrency": 3,
            "candidate_thinking": "disabled", "candidate_max_tokens": 1800,
            "portfolio_thinking": "disabled", "portfolio_max_tokens": 3200,
            "candidate_codes": [normalize_ts_code(row.stock_code) for row in ordered],
            "selection_sources": context.selection_sources,
            "flash_v4_run_id": FLASH_RUN_ID, "quant_rerun": False,
        },
        portfolio_result={}, warnings=["MODEL_VALIDATION", "NON_ACTIONABLE"],
    )
    session.add(row)
    session.commit()
    return row


def _flash_v4_usage(session) -> dict[str, int | float]:
    run_ids = [CANARY_RUN_ID, FLASH_RUN_ID, RETRY_RUN_ID]
    rows = list(session.scalars(select(ModelValidationLLMAudit).where(
        ModelValidationLLMAudit.validation_run_id.in_(run_ids),
        ModelValidationLLMAudit.cache_status != "RETRY_RESOLVED",
    )))
    return {
        "actual_tokens": sum(int(row.input_tokens or 0) + int(row.output_tokens or 0) for row in rows),
        "repair_tokens": sum(
            int((row.diagnostics or {}).get("repair_input_tokens") or 0)
            + int((row.diagnostics or {}).get("repair_output_tokens") or 0) for row in rows
        ),
        "cost_usd": round(sum(float(row.cost_usd or 0) for row in rows), 8),
        "unavailable_call_count": 10,
    }


def _pro_usage(session, run_id: str) -> dict[str, int | float]:
    rows = list(session.scalars(select(LLMUsage).where(LLMUsage.pro_resume_run_id == run_id)))
    return {
        "calls": len(rows), "tokens": sum(int(row.total_tokens or 0) for row in rows),
        "cost_usd": round(sum(float(row.cost_usd or 0) for row in rows), 8),
    }


def _resolved_history(session) -> list[dict]:
    rows = list(session.scalars(select(ModelValidationLLMAudit).where(
        ModelValidationLLMAudit.validation_run_id == FLASH_RUN_ID,
        ModelValidationLLMAudit.schema_status == "RESOLVED_HISTORY",
    )))
    return [{
        "stock_code": row.stock_code, "task": row.task, "status": "RESOLVED_HISTORY",
        "error_category": row.error_category, "resolution": "V4 targeted retry succeeded",
    } for row in rows]


def _current_warnings(payload) -> list[dict]:
    return [
        {"stock_code": row["stock_code"], "warning": row["order_warning"] or row["position_warning"]}
        for row in payload["order_rows"] if row["order_warning"] or row["position_warning"]
    ]


def _runtime_switches() -> dict[str, str]:
    return {
        "LLM_REAL_CALLS_ENABLED": os.getenv("LLM_REAL_CALLS_ENABLED", "false"),
        "RUN_REAL_FUNDAMENTAL_RESEARCH": os.getenv("RUN_REAL_FUNDAMENTAL_RESEARCH", "false"),
        "LLM_GATEWAY_MOCK_ONLY": os.getenv("LLM_GATEWAY_MOCK_ONLY", "true"),
    }


def _report(session, context, resume, payload, validation, final_path, audit_path, workbook_hash,
            resolved, canary_reports, review_reports, portfolio_report, v4_usage, pro_usage,
            pro_usage_before, total_known):
    scores = sorted(float(row["llm_score"]) for row in payload["llm_rows"] if row["llm_score"] is not None)
    percentile = lambda p: scores[min(len(scores) - 1, round((len(scores) - 1) * p))]
    decisions = Counter(row["decision"] for row in payload["llm_rows"])
    fundamentals = payload["fundamental_rows"]
    orders = payload["order_rows"]
    zero = [row for row in orders if int(row["quantity"] or 0) == 0]
    return {
        "phase": "Flash V4 Meaningful Scoring + Fundamental Enrichment + Order Risk-Reward Correction",
        "status": "PARTIAL_SUCCESS", "completed": True,
        "baseline": {"quant_run_id": context.quant_run.run_id, "quant_scored": 5308, "quant_rerun": False},
        "concept_mapping_audit": {"structured_tags_in_candidates": sum("*" not in row["concept_tags"] for row in fundamentals)},
        "fundamental_v4": {"resolved_retry_codes": resolved, "current_failure_count": 2},
        "flash_v4_run_id": FLASH_RUN_ID,
        "flash_score_distribution": {"min": scores[0], "p25": percentile(.25), "median": percentile(.5), "p75": percentile(.75), "max": scores[-1], "unique_score_count": len(set(scores))},
        "flash_decision_distribution": {key: decisions.get(key, 0) for key in ("ADVANCE", "HOLD", "WATCH_ONLY", "REJECT", "BLOCK")},
        "data_quality_scale": "0-100",
        "new_top20": context.top20_codes, "top20_hash": context.top20_hash,
        "candidate_count": len(context.candidates), "candidate_set_hash": context.candidate_set_hash,
        "pro_v3": {"run_id": resume.run_id, "count": len(context.candidates), "ranking_version": RANKING_VERSION, "portfolio": portfolio_report, "usage_before": pro_usage_before},
        "order_risk_reward_correction": {"active_target_mode": "TAKE_PROFIT_2", "minimum": 1.5, "count": len(orders)},
        "stop_loss_rounding_correction": {"tolerance_ticks": 1},
        "position_sizing": {
            "candidate_count": len(orders), "non_zero_quantity_count": len(orders) - len(zero),
            "zero_due_risk_reward": sum("RISK_REWARD_BELOW_MINIMUM" in row["position_warning"] for row in zero),
            "zero_due_stop_conflict": sum("UPSTREAM_STOP_LOSS_CONFLICT" in row["position_warning"] for row in zero),
            "zero_due_hard_risk": sum("RISK_GATE" in row["position_warning"] for row in zero),
            "zero_due_liquidity_or_concentration": sum(any(value in row["position_warning"] for value in ("LIQUIDITY", "CONCENTRATION", "TRADING_LOT")) for row in zero),
        },
        "fundamental_semantic_completeness": {
            "industry_chain_known_count": sum(row["industry_chain"] not in {"UNKNOWN", "信息不足*"} for row in fundamentals),
            "core_products_known_count": sum(row["core_products"] != "信息不足*" for row in fundamentals),
            "concept_tags_known_count": sum(row["concept_tags"] != "信息不足*" for row in fundamentals),
            "competitive_advantage_known_count": sum("信息不足" not in row["competitive_advantage"] and row["competitive_advantage"] != "UNKNOWN" for row in fundamentals),
            "investment_logic_known_count": sum("信息不足" not in row["investment_logic"] and row["investment_logic"] != "UNKNOWN" for row in fundamentals),
        },
        "excel_state_cleanup": {"current_errors": len(payload["errors"]), "resolved_historical_errors": len(payload["resolved_historical_errors"])},
        "checkpoint_update": "COMPLETED", "workbook_row_counts": {"quant": len(payload["quant_rows"]), "flash": len(payload["llm_rows"]), "top20": 20, "manual": 7, "candidate": len(orders), "order": len(orders), "position": len(orders), "fundamental": len(fundamentals)},
        "usage_and_cost": {"flash_v4": v4_usage, "pro_v3": pro_usage, "pipeline_total_known_tokens": total_known, "remaining_known_budget": 5_000_000-total_known},
        "runtime_switch_restoration": _runtime_switches(),
        "excel_output": str(final_path), "audit_output": str(audit_path), "workbook_sha256": workbook_hash,
        "excel_validation": validation,
        "pro_canary_count": len(canary_reports), "pro_review_report_count": len(review_reports),
        "problems": ["Two Fundamental V4 outputs remain failed after one targeted retry", "Initial five-stock canary persistence failed before audit commit; ten calls have unavailable usage"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
