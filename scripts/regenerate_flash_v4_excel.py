from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from database.models.validation import ModelValidationSample, ProResumeRun
from database.session import get_session, init_db
from scripts.complete_flash_v4_pipeline import (
    CHECKPOINT,
    FLASH_RUN_ID,
    REPORT,
    _flash_v4_usage,
    _pro_usage,
    _resolved_history,
    _runtime_switches,
)
from scripts.run_guarded_llm_excel_validation import _secret_scan
from scripts.run_trader_demo_excel import _build_excel, _serialize_readback, _sidecar, _validate_xlsx
from trader_demo.resume_checkpoint import load_resume_context
from trader_demo.service import TraderDemoService


EXPECTED_SWITCHES = {
    "LLM_REAL_CALLS_ENABLED": "false",
    "RUN_REAL_FUNDAMENTAL_RESEARCH": "false",
    "LLM_GATEWAY_MOCK_ONLY": "true",
}


def main() -> int:
    if _runtime_switches() != EXPECTED_SWITCHES:
        raise RuntimeError("LOCAL_EXCEL_REGEN_REQUIRES_MOCK_ONLY_RUNTIME")
    init_db()
    session = get_session()
    try:
        context = load_resume_context(session, FLASH_RUN_ID)
        resume = session.scalar(select(ProResumeRun).where(
            ProResumeRun.flash_validation_run_id == FLASH_RUN_ID,
            ProResumeRun.candidate_set_hash == context.candidate_set_hash,
            ProResumeRun.status == "COMPLETED",
        ).order_by(ProResumeRun.id.desc()))
        if resume is None:
            raise ValueError("COMPLETED_PRO_V3_RUN_REQUIRED")

        payload = _serialize_readback(TraderDemoService(session).readback(FLASH_RUN_ID))
        payload["resolved_historical_errors"] = _resolved_history(session)
        flash_usage = _flash_v4_usage(session)
        pro_usage = _pro_usage(session, resume.run_id)
        total_known = 934_586 + int(flash_usage["actual_tokens"]) + int(pro_usage["tokens"])
        payload["budget"] = {
            "limits": {"daily_limit": 5_000_000, "warning_threshold": 4_000_000, "final_reserve": 300_000},
            "usage": {"flash": flash_usage["actual_tokens"], "pro": pro_usage["tokens"], "repair": flash_usage["repair_tokens"], "connectivity": 288},
            "total": total_known,
            "remaining": 5_000_000 - total_known,
            "warning_triggered": total_known >= 4_000_000,
        }
        payload["pro_resume"] = {
            "run_id": resume.run_id,
            "candidate_set_hash": context.candidate_set_hash,
            "portfolio_result": resume.portfolio_result,
            "token_ledger": {
                "current_resume_new_api_tokens": pro_usage["tokens"],
                "pipeline_total_actual_api_tokens": total_known,
                "remaining_budget_from_known_actual": 5_000_000 - total_known,
                "pro_v1_unavailable_count": 1 + int(flash_usage["unavailable_call_count"]),
            },
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        payload["content_hash"] = hashlib.sha256(serialized.encode()).hexdigest()

        output_dir = ROOT / "outputs" / "2026-07-10" / "历史版本" / "完整流水线"
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / "ai_trader_flash_v4_20260710_state_clean.xlsx"
        if final_path.exists():
            raise FileExistsError(f"OUTPUT_EXISTS:{final_path.name}")
        temp_path = output_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp.xlsx"
        _build_excel(payload, temp_path, output_dir)
        validation = _validate_xlsx(temp_path, payload)
        os.replace(temp_path, final_path)
        workbook_hash = hashlib.sha256(final_path.read_bytes()).hexdigest()

        sidecar = _sidecar(payload, validation, final_path, workbook_hash)
        sidecar["current_errors"] = payload["errors"]
        sidecar["current_warnings"] = payload.get("warnings") or []
        sidecar["resolved_historical_errors"] = payload["resolved_historical_errors"]
        sidecar_text = json.dumps(sidecar, ensure_ascii=False, indent=2, default=str)
        if not _secret_scan(sidecar_text):
            raise ValueError("FLASH_V4_SIDECAR_SECRET_SCAN_FAILED")
        audit_path = final_path.with_name(final_path.stem + "_audit.json")
        audit_path.write_text(sidecar_text, encoding="utf-8")

        checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        checkpoint.update({
            "stage": "COMPLETED",
            "final_status": "PARTIAL_SUCCESS",
            "excel_path": str(final_path),
            "workbook_sha256": workbook_hash,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        CHECKPOINT.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")

        report = json.loads(REPORT.read_text(encoding="utf-8"))
        scores = sorted(float(row["llm_score"]) for row in payload["llm_rows"] if row["llm_score"] is not None)
        percentile = lambda value: scores[min(len(scores) - 1, round((len(scores) - 1) * value))]
        decisions = Counter(row["decision"] for row in payload["llm_rows"])
        zero_rows = [row for row in payload["order_rows"] if int(row["quantity"] or 0) == 0]
        concept_statuses = Counter()
        candidate_concept_statuses = Counter()
        for sample in session.scalars(select(ModelValidationSample).where(
            ModelValidationSample.validation_run_id == FLASH_RUN_ID
        )):
            audit = (sample.fundamental_result or {}).get("concept_mapping_audit") or {}
            status = str(audit.get("concept_source_status") or "UNKNOWN")
            concept_statuses[status] += 1
            if ((sample.screening_result or {}).get("_trader_demo") or {}).get("selection_source"):
                candidate_concept_statuses[status] += 1
        report.update({
            "flash_score_distribution": {
                "min": scores[0], "p25": percentile(.25), "median": percentile(.5),
                "p75": percentile(.75), "max": scores[-1], "unique_score_count": len(set(scores)),
            },
            "flash_decision_distribution": {
                key: decisions.get(key, 0) for key in ("ADVANCE", "HOLD", "WATCH_ONLY", "REJECT", "BLOCK")
            },
            "excel_state_cleanup": {
                "current_errors": len(payload["errors"]),
                "current_warnings": len(payload.get("warnings") or []),
                "resolved_historical_errors": len(payload["resolved_historical_errors"]),
            },
            "concept_mapping_audit": {
                "evaluated_count": sum(concept_statuses.values()),
                "mixed_verified_and_inferred_count": concept_statuses["MIXED_VERIFIED_AND_LLM_UNVERIFIED"],
                "llm_unverified_only_count": concept_statuses["LLM_UNVERIFIED"],
                "unknown_count": concept_statuses["UNKNOWN"],
                "candidate_mixed_verified_and_inferred_count": candidate_concept_statuses["MIXED_VERIFIED_AND_LLM_UNVERIFIED"],
                "candidate_llm_unverified_only_count": candidate_concept_statuses["LLM_UNVERIFIED"],
                "candidate_unknown_count": candidate_concept_statuses["UNKNOWN"],
            },
            "position_sizing": {
                **dict(report.get("position_sizing") or {}),
                "candidate_count": len(payload["order_rows"]),
                "non_zero_quantity_count": sum(int(row["quantity"] or 0) > 0 for row in payload["order_rows"]),
                "zero_due_risk_reward": sum("RISK_REWARD_BELOW_MINIMUM" in row["position_warning"] for row in zero_rows),
                "zero_due_stop_conflict": sum("UPSTREAM_STOP_LOSS_CONFLICT" in row["position_warning"] for row in zero_rows),
                "zero_due_hard_risk": sum("RISK_GATE_BLOCKED" in row["position_warning"] for row in zero_rows),
                "zero_due_liquidity_or_concentration": sum(
                    any(marker in row["position_warning"] for marker in ("BELOW_ONE_TRADING_LOT", "LIQUIDITY", "CONCENTRATION"))
                    for row in zero_rows
                ),
            },
            "usage_and_cost": {
                "flash_v4": flash_usage,
                "pro_v3": pro_usage,
                "pipeline_total_known_tokens": total_known,
                "remaining_known_budget": 5_000_000 - total_known,
            },
            "excel_output": str(final_path),
            "audit_output": str(audit_path),
            "workbook_sha256": workbook_hash,
            "excel_validation": validation,
            "problems": [
                "603726.SH and 688222.SH Fundamental V4 outputs remain failed after one targeted retry",
                "Initial five-stock canary persistence failed before audit commit; ten calls have unavailable usage",
            ],
        })
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({
            "status": "PARTIAL_SUCCESS",
            "current_errors": len(payload["errors"]),
            "current_warnings": len(payload.get("warnings") or []),
            "resolved_historical_errors": len(payload["resolved_historical_errors"]),
            "llm_success_count": payload["row_counts"]["llm_success_count"],
            "llm_failure_count": payload["row_counts"]["llm_failure_count"],
            "non_zero_quantity_count": sum(int(row["quantity"] or 0) > 0 for row in payload["order_rows"]),
            "validation": validation,
            "excel_path": str(final_path),
            "audit_path": str(audit_path),
            "workbook_sha256": workbook_hash,
            "runtime_switches": _runtime_switches(),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
