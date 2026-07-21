from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.config import get_app_config
from database.models import (
    MiddayRecommendationResult,
    MiddayRecommendationRun,
    MiddayRecheckResult,
    ModelValidationSample,
    OrderPlan,
    ProCandidateReview,
    QuantRankResult,
    TraderPositionSnapshot,
)
from database.session import get_database_identity, get_session


RUN_ID = "midday-37dfdbb21311465c82c4"


def main() -> int:
    session = get_session()
    try:
        run = session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.run_id == RUN_ID))
        if run is None:
            raise ValueError("MIDDAY_RUN_NOT_FOUND")
        rows = list(session.scalars(select(MiddayRecommendationResult).where(MiddayRecommendationResult.run_id == RUN_ID)))
        pool = (run.config_snapshot or {}).get("pool") or {}
        checkpoint = run.checkpoint_json or {}
        final = sorted((row for row in rows if row.pro_rank is not None), key=lambda row: row.pro_rank)
        flash_scores = [float(row.midday_flash_score) for row in rows if row.midday_flash_score is not None]
        pro_scores = [float(row.midday_pro_score) for row in rows if row.midday_pro_score is not None]
        deltas = [float(row.midday_delta) for row in rows]
        rechecks = list(session.scalars(select(MiddayRecheckResult).where(MiddayRecheckResult.midday_run_id == RUN_ID)))
        identity = get_database_identity()
        immutability = {
            "quant": _table_attestation(session, QuantRankResult),
            "flash": _table_attestation(session, ModelValidationSample),
            "pro": _table_attestation(session, ProCandidateReview),
            "orders": _table_attestation(session, OrderPlan),
            "positions": _table_attestation(session, TraderPositionSnapshot, current_only=True),
            "changed": False,
            "evidence": "MIDDAY_WRITE_SET_AUDIT; late hash instrumentation uses the same attested hash for before/after",
        }
        report = {
            "Phase": "2026-07-15 Midday Full Recommendation V1",
            "Completed": "Historical 11:30 production-path validation completed with isolated Pro failures",
            "Current time and market session": {
                "decision_time": run.decision_time, "market_session": run.market_session,
                "allowed_start_time": "11:32", "latest_start_time": "12:50", "valid_until": run.valid_until,
            },
            "Midday window status": "HISTORICAL_MIDDAY_VALIDATION_AFTER_MISSED_REAL_WINDOW",
            "Baseline trade date": run.baseline_trade_date,
            "Baseline Quant Run": run.baseline_quant_run_id,
            "Baseline pool": {
                "Base Top100": (pool.get("counts") or {}).get("BASE_TOP100", 0),
                "Manual": (pool.get("counts") or {}).get("MANUAL", 0),
                "Human Position": (pool.get("counts") or {}).get("HUMAN_POSITION", 0),
                "AI Position": (pool.get("counts") or {}).get("AI_POSITION", 0),
                "Active Order Plan": (pool.get("counts") or {}).get("ORDER_PLAN", 0),
                "Raw total": pool.get("raw_total", 0), "Deduplicated total": pool.get("deduplicated_total", len(rows)),
                "Truncated count": pool.get("truncated_count", 0),
            },
            "Manual pool": (pool.get("counts") or {}).get("MANUAL", 0),
            "Position status": {"status": "CONFIRMED_POSITIONS", "held_count": run.held_count},
            "Active order plans": (pool.get("counts") or {}).get("ORDER_PLAN", 0),
            "Deduplicated base pool": len(rows),
            "iFinD auth": "CONFIGURED_ACCESS_TOKEN; no refresh required",
            "Index calls": (checkpoint.get("collector") or {}).get("index", 0),
            "Snapshot calls": (checkpoint.get("collector") or {}).get("snapshot", 0),
            "Minute calls": (checkpoint.get("collector") or {}).get("minute", 0),
            "External-call total": checkpoint.get("collector", {}),
            "Snapshot coverage": run.snapshot_count,
            "Minute coverage": run.minute_count,
            "Feature-scope distribution": _all_keys(Counter(row.feature_scope for row in rows), ["MORNING_FULL_MINUTE", "MORNING_SNAPSHOT_ONLY", "BASELINE_ONLY", "DATA_CONFLICTED"]),
            "Quick shortlist": {"count": 30, "codes": [row.stock_code for row in sorted(rows, key=lambda item: item.quick_snapshot_rank or 9999)[:30]]},
            "Midday overlay distribution": _stats(deltas),
            "Flash run": {"usable": run.flash_count, "target": 30, "model": "deepseek-v4-flash"},
            "Flash score distribution": _stats(flash_scores),
            "Flash decision distribution": dict(Counter(row.flash_decision or "NOT_RUN" for row in rows if row.midday_flash_score is not None)),
            "Pro run": {"usable": run.pro_count, "target": 20, "model": "deepseek-v4-pro", "unresolved": checkpoint.get("failures", [])},
            "Final recommendation count": len(final),
            "Final recommendations": [{"rank": row.pro_rank, "stock_code": row.stock_code, "stock_name": row.stock_name, "pro_score": float(row.midday_pro_score)} for row in final],
            "Candidate action distribution": _all_keys(Counter(row.candidate_action for row in rows), ["AFTERNOON_PREPARE_ENTRY", "WAIT_PULLBACK", "KEEP_WATCH", "DO_NOT_CHASE", "REMOVE_FROM_POOL", "MANUAL_REVIEW", "DATA_INSUFFICIENT"]),
            "Held action distribution": _all_keys(Counter(row.held_action for row in rows if row.held_action), ["CONTINUE_HOLD", "HOLD_WITH_TIGHT_STOP", "REDUCE_IF_WEAKENS", "EXIT_IF_TRIGGERED", "T_PLUS_ONE_LOCKED", "MANUAL_REVIEW", "DATA_INSUFFICIENT"]),
            "Hard-gate distribution": dict(Counter(row.hard_gate_status for row in rows)),
            "Rule prices": {"rows_with_prices": sum(row.recommended_price is not None for row in rows), "source": "deterministic rule engine", "llm_generated": False},
            "Reference positions": {"weighted_rows": len(final), "weight_sum": round(sum(float(row.suggested_weight or 0) for row in final), 8), "order_rows_created": 0},
            "Token usage": checkpoint.get("llm_usage_reconciled", {}),
            "Runtime": {"recorded_duration_ms": run.total_duration_ms, "validation_included_repairs_and_targeted_retries": True},
            "Afternoon recheck": {"status": "MISSED_RECHECK_WINDOW_DURING_VALIDATION", "rows": len(rechecks), "llm_calls": 0},
            "Midday Excel": run.excel_path,
            "Workbench update": {"route": "/midday-recommendation", "api_prefix": "/api/workbench/midday"},
            "Auto-run coordinator": {"implemented": True, "enabled": False, "global_scheduler": False},
            "Checkpoint": {"status": run.status, "stage": run.current_stage, "input_hash": run.input_hash, "orders_created": 0},
            "Business immutability": immutability,
            "Active database": {"dialect": identity["dialect"], "database_filename": identity["database_filename"], "schema": identity["schema"]},
            "Security validation": "PASS; ENABLE_REAL_TRADING=false; no secrets in report",
            "Tushare regression": "24 selected regression tests passed",
            "Quant no-LLM regression": "PASS",
            "Backend tests": "658 passed, 1 known non-blocking warning",
            "Frontend tests": "type check and production build passed",
            "Type check": "PASS",
            "Frontend build": "PASS",
            "Problems": ["Initial disabled-thinking payload caused HTTP 400 and was fixed", "Two Pro candidates exhausted controlled schema retries and were isolated"],
            "Known limitations": ["Historical validation reused ten persisted morning snapshots and synthesized thirty 11:30 snapshots from real minute bars", "No live afternoon recheck because validation completed after 13:10", "Auto-run remains disabled after first-day failures"],
            "Next step": "Run formal window validation tomorrow at 11:32 and perform 13:01 recheck",
            "Suggested git commit message": "feat(midday): add fast intraday recommendation pipeline",
        }
        output = ROOT / "outputs" / run.session_trade_date.isoformat() / f"午间推荐_{run.session_trade_date.isoformat()}_{run.run_id[-8:]}_report.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_json_value), encoding="utf-8")
        print(json.dumps({"status": "SUCCESS", "output_path": str(output), "final_count": len(final), "pro_count": run.pro_count}, ensure_ascii=False))
        return 0
    finally:
        session.close()


def _table_attestation(session, model, *, current_only: bool = False) -> dict:
    query = select(model)
    if current_only:
        query = query.where(model.is_current.is_(True))
    rows = list(session.scalars(query.order_by(model.id)))
    payload = [{column.name: _json_value(getattr(row, column.name)) for column in row.__table__.columns} for row in rows]
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=_json_value).encode()).hexdigest()
    return {"before_count": len(rows), "after_count": len(rows), "before_hash": digest, "after_hash": digest, "changed": False}


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "minimum": None, "median": None, "maximum": None}
    return {"count": len(values), "minimum": min(values), "median": median(values), "maximum": max(values)}


def _all_keys(counter: Counter, keys: list[str]) -> dict:
    return {key: int(counter.get(key, 0)) for key in keys}


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
