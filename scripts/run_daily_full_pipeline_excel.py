from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import func, select


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env", override=False)

from backend.core.config_manager import ConfigManager
from database.models.quant_run import QuantRankResult, QuantRun
from database.models.stock import StockMaster
from database.models.system import ConfigHistory, LLMUsage, SystemConfig
from database.models.temporal import RunDataManifestRecord
from database.models.validation import (
    ModelValidationLLMAudit,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
    ProCandidateReview,
    ProResumeRun,
)
from database.session import get_session, init_db
from database.stock_master_sync import ManualStockResolver
from research.knowledge_mode import LLMKnowledgeMode
from scripts.run_guarded_llm_excel_validation import _run_real_preflight, _secret_scan
from scripts.run_trader_demo_excel import (
    _build_excel,
    _serialize_readback,
    _sidecar,
    _validate_xlsx,
)
from stock_codes import normalize_ts_code
from trader_demo.budget import (
    PipelineBudgetConfig,
    PipelineBudgetExceeded,
    PipelineBudgetManager,
    canary_projection,
)
from trader_demo.pro_aggregation import ProAggregationError, ProAggregationService
from trader_demo.runtime import temporary_real_llm_runtime
from trader_demo.pro_resume import (
    PRO_CANDIDATE_PROMPT_VERSION,
    PRO_CONTRACT_VERSION,
    PRO_PORTFOLIO_PROMPT_VERSION,
    ProResumeService,
    ProStageFailure,
    chunk_candidates,
)
from trader_demo.resume_checkpoint import load_resume_context, validate_or_upgrade_checkpoint
from trader_demo.service import ManualSelection, TraderDemoService
from trader_demo.usage_ledger import AuthoritativeUsageLedger, PIPELINE_RUN_ID
from trader_demo.pro_single_v3 import (
    PORTFOLIO_CONTRACT_VERSION,
    PORTFOLIO_PROMPT_VERSION as PORTFOLIO_PROMPT_VERSION_V3,
    PREVIOUS_FAILED_RUN_ID,
    RANKING_VERSION,
    SINGLE_CONTRACT_VERSION,
    SINGLE_PROMPT_VERSION,
    ProSingleV3Failure,
    ProPortfolioWireV3,
    ProSingleV3Service,
    ensure_v3_schema,
    select_v3_canary,
    stable_v3_order,
    _single_input,
)


QUANT_RUN_ID = "quant-840384b9e637e1143f243083"
MANUAL_STOCKS = (
    ("南方泵业", "300145.SZ"),
    ("东岳硅材", "300821.SZ"),
    ("冠龙节能", "301151.SZ"),
    ("天振股份", "301356.SZ"),
    ("朗迪集团", "603726.SH"),
    ("中科曙光", "603019.SH"),
    ("雅克科技", "002409.SZ"),
)
CANARY_RANKS = (1, 50, 100, 10, 545)
CONFIG_REASON = "Increase daily LLM token budget to 5M for full pipeline validation"
QUANT_REPORT = ROOT_DIR / "data" / "reports" / "temporal_quant_top500_report.json"
REPORT_PATH = ROOT_DIR / "data" / "reports" / "daily_full_pipeline_20260710_report.json"


class CheckpointRecorder:
    def __init__(self, manager: PipelineBudgetManager, path: Path, stage_name: str) -> None:
        self.manager = manager
        self.path = path
        self.stage_name = stage_name
        self.previous = {"flash_input": 0, "flash_output": 0, "repair_input": 0, "repair_output": 0}

    def __call__(self, checkpoint: dict[str, Any]) -> None:
        repair_input = int(checkpoint.get("repair_input_tokens") or 0)
        repair_output = int(checkpoint.get("repair_output_tokens") or 0)
        current = {
            "flash_input": max(0, int(checkpoint.get("input_tokens") or 0) - repair_input),
            "flash_output": max(0, int(checkpoint.get("output_tokens") or 0) - repair_output),
            "repair_input": repair_input,
            "repair_output": repair_output,
        }
        self.manager.record(
            "flash",
            input_tokens=current["flash_input"] - self.previous["flash_input"],
            output_tokens=current["flash_output"] - self.previous["flash_output"],
        )
        repair_delta = (
            current["repair_input"] - self.previous["repair_input"],
            current["repair_output"] - self.previous["repair_output"],
        )
        if any(repair_delta):
            self.manager.record("repair", input_tokens=repair_delta[0], output_tokens=repair_delta[1])
        self.previous = current
        payload = {
            "stage": self.stage_name,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "batch": checkpoint,
            "budget": self.manager.snapshot(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = _parser().parse_args()
    if args.resume_from_checkpoint:
        if args.new_pro_contract == SINGLE_CONTRACT_VERSION:
            return _resume_v3_main(args)
        return _resume_main(args)
    report: dict[str, Any] = {
        "phase": "2026-07-10 Full Daily Pipeline",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "stopped_stages": [],
        "problems": [],
    }
    output_dir = (ROOT_DIR / args.output_dir).resolve()
    checkpoint_path = output_dir / "daily_full_pipeline_checkpoint.json"
    session = None
    try:
        _validate_args(args)
        init_db()
        session = get_session()
        service = TraderDemoService(session)
        baseline = _validate_baseline(session, service, args)
        report.update(baseline)

        config = PipelineBudgetConfig(
            daily_limit=args.daily_token_budget,
            warning_percent=args.token_warning_percent,
            flash_limit=args.flash_token_budget,
            pro_limit=args.pro_token_budget,
            repair_limit=args.repair_token_budget,
            final_reserve=args.final_token_reserve,
        )
        budget = PipelineBudgetManager(config)
        prior_connectivity = _prior_connectivity_tokens()
        if prior_connectivity:
            budget.record("connectivity", input_tokens=prior_connectivity, output_tokens=0)
        report["token_carryover"] = {"connectivity": prior_connectivity}
        estimate = _initial_budget_estimate(service, baseline["pool_rows"], config)
        report["budget_estimate"] = estimate
        budget.assert_projection(
            flash=estimate["reserved_flash_tokens"],
            pro=estimate["reserved_pro_tokens"],
            repair=estimate["reserved_repair_tokens"],
        )

        if not args.real_llm or not args.confirm_budget_warning:
            report["status"] = "DRY_RUN"
            _write_report(report)
            print(json.dumps(_public_report(report), ensure_ascii=False, indent=2, default=str))
            return 0

        with temporary_real_llm_runtime():
            preflight = _run_real_preflight()
            report["connectivity"] = preflight
            if preflight.get("status") != "PASS":
                raise RuntimeError(f"CONNECTIVITY_CANARY_FAILED:{preflight.get('reason')}")
            connectivity_usage = (preflight.get("connectivity") or {}).get("token_usage") or {}
            budget.record(
                "connectivity",
                input_tokens=int(connectivity_usage.get("input_tokens") or 0),
                output_tokens=int(connectivity_usage.get("output_tokens") or 0),
            )
            report["token_carryover"]["connectivity"] = prior_connectivity + int(
                connectivity_usage.get("total_tokens") or 0
            )

            canary_manual = [
                ManualSelection(code, reason="FULL_PIPELINE_CANARY", priority="CANARY")
                for code in ("603019.SH", "002409.SZ")
            ]
            canary_recorder = CheckpointRecorder(budget, checkpoint_path, "CANARY")
            canary_run_id = service.run(
                quant_run_id=QUANT_RUN_ID,
                ranks=CANARY_RANKS,
                top_n=None,
                manual=canary_manual,
                selected_decisions=set(),
                account_equity=Decimal(args.account_equity),
                available_cash=Decimal(args.available_cash),
                continue_on_stock_error=False,
                reuse_successful=True,
                knowledge_mode=LLMKnowledgeMode.LLM_UNVERIFIED_CURRENT,
                analysis_only=True,
                concurrency=args.concurrency,
                batch_size=args.batch_size,
                checkpoint_callback=canary_recorder,
            )
            canary = _validate_canary(session, canary_run_id)
            if canary["reused_input_tokens"] or canary["reused_output_tokens"]:
                budget.record(
                    "flash",
                    input_tokens=canary["reused_input_tokens"] - canary["reused_repair_input_tokens"],
                    output_tokens=canary["reused_output_tokens"] - canary["reused_repair_output_tokens"],
                )
                if canary["reused_repair_input_tokens"] or canary["reused_repair_output_tokens"]:
                    budget.record(
                        "repair",
                        input_tokens=canary["reused_repair_input_tokens"],
                        output_tokens=canary["reused_repair_output_tokens"],
                    )
            report["canary"] = canary

            projection = _post_canary_projection(
                canary["task_usages"], total_flash_tasks=len(baseline["pool_rows"]) * 2,
                connectivity_tokens=int(connectivity_usage.get("total_tokens") or 0),
            )
            report["post_canary_projection"] = projection
            budget.assert_projection(
                flash=projection["projected_flash_total"],
                pro=projection["projected_pro_total"],
                repair=projection["projected_repair_total"],
            )
            if projection["projected_grand_total"] > config.regular_task_stop:
                raise PipelineBudgetExceeded("PROJECTED_TOTAL_TOKEN_SAFETY_LIMIT_EXCEEDED")

            manual = [
                ManualSelection(code, reason="USER_MANUAL_POOL", priority="MANDATORY_REVIEW")
                for _, code in MANUAL_STOCKS
            ]
            validation_run_id = _find_resumable_full_run(session)
            if validation_run_id:
                selected_codes = service.persist_model_validation_top_n(
                    validation_run_id, top_n=args.llm_select_n, manual=manual
                )
                existing_usage = _existing_flash_usage(session, validation_run_id)
                budget.record(
                    "flash",
                    input_tokens=existing_usage["input_tokens"] - existing_usage["repair_input_tokens"],
                    output_tokens=existing_usage["output_tokens"] - existing_usage["repair_output_tokens"],
                )
                if existing_usage["repair_input_tokens"] or existing_usage["repair_output_tokens"]:
                    budget.record(
                        "repair",
                        input_tokens=existing_usage["repair_input_tokens"],
                        output_tokens=existing_usage["repair_output_tokens"],
                    )
                report["flash_resume"] = {
                    "validation_run_id": validation_run_id,
                    "llm_calls_repeated": 0,
                    "persisted_top20_count": len(selected_codes),
                    "accounted_usage": existing_usage,
                }
            else:
                full_recorder = CheckpointRecorder(budget, checkpoint_path, "FLASH_FULL")
                validation_run_id = service.run(
                    quant_run_id=QUANT_RUN_ID,
                    ranks=None,
                    top_n=args.quant_top_n,
                    manual=manual,
                    selected_decisions={"ADVANCE", "HOLD", "WATCH_ONLY", "REJECT"},
                    account_equity=Decimal(args.account_equity),
                    available_cash=Decimal(args.available_cash),
                    continue_on_stock_error=args.continue_on_stock_error,
                    reuse_successful=True,
                    knowledge_mode=LLMKnowledgeMode.LLM_UNVERIFIED_CURRENT,
                    model_validation_top_n=args.llm_select_n,
                    defer_candidate_generation=True,
                    concurrency=args.concurrency,
                    batch_size=args.batch_size,
                    checkpoint_callback=full_recorder,
                )
            flash_summary = _validate_full_flash(session, validation_run_id, args.llm_select_n)
            report["flash_batch"] = flash_summary

            samples = list(session.scalars(
                select(ModelValidationSample)
                .where(ModelValidationSample.validation_run_id == validation_run_id)
                .order_by(ModelValidationSample.rank)
            ))
            candidates = [
                sample for sample in samples
                if (sample.screening_result or {}).get("_trader_demo", {}).get("selection_source")
            ]
            sources = {
                normalize_ts_code(sample.stock_code): str(
                    sample.screening_result["_trader_demo"]["selection_source"]
                )
                for sample in candidates
            }
            score_snapshot = _score_snapshot(candidates)
            pro_service = ProAggregationService()
            try:
                pro_result, pro_usage, pro_audit = pro_service.run(
                    candidates, selection_sources=sources, model_alias=args.pro_model_alias
                )
            except ProAggregationError as exc:
                session.add(pro_service.audit_row(validation_run_id, exc.audit))
                session.commit()
                report["pro_aggregation"] = {
                    "status": "FAILED", "stock_count": len(candidates),
                    "actual_model": exc.audit.get("actual_model"),
                    "input_tokens": exc.usage.input_tokens,
                    "output_tokens": exc.usage.output_tokens,
                    "repair_input_tokens": exc.usage.repair_input_tokens,
                    "repair_output_tokens": exc.usage.repair_output_tokens,
                    "cost_usd": exc.usage.cost_usd,
                    "schema_status": exc.audit.get("schema_status"),
                }
                raise
            base_pro_input = pro_usage.input_tokens - pro_usage.repair_input_tokens
            base_pro_output = pro_usage.output_tokens - pro_usage.repair_output_tokens
            budget.record("pro", input_tokens=base_pro_input, output_tokens=base_pro_output)
            if pro_usage.repair_input_tokens or pro_usage.repair_output_tokens:
                budget.record(
                    "repair",
                    input_tokens=pro_usage.repair_input_tokens,
                    output_tokens=pro_usage.repair_output_tokens,
                )
            if pro_audit.get("actual_model") != "deepseek-v4-pro":
                raise ValueError(f"PRO_ACTUAL_MODEL_MISMATCH:{pro_audit.get('actual_model')}")
            pro_service.apply_to_samples(candidates, pro_result)
            if _score_snapshot(candidates) != score_snapshot:
                raise ValueError("PRO_MUTATED_QUANT_OR_FLASH_SCORE")
            session.add(pro_service.audit_row(validation_run_id, pro_audit))
            run_row = session.scalar(select(ModelValidationRun).where(ModelValidationRun.run_id == validation_run_id))
            snapshot = dict(run_row.config_snapshot or {})
            snapshot.update({"budget": budget.snapshot(), "pro_model_alias": args.pro_model_alias})
            run_row.config_snapshot = snapshot
            session.commit()
            report["pro_aggregation"] = {
                "status": "SUCCESS", "stock_count": len(candidates),
                "actual_model": pro_audit.get("actual_model"),
                "input_tokens": pro_usage.input_tokens,
                "output_tokens": pro_usage.output_tokens,
                "repair_input_tokens": pro_usage.repair_input_tokens,
                "repair_output_tokens": pro_usage.repair_output_tokens,
                "cost_usd": pro_usage.cost_usd,
            }

            service.generate_candidate_outputs(
                validation_run_id,
                account_equity=Decimal(args.account_equity),
                available_cash=Decimal(args.available_cash),
            )
            payload = _serialize_readback(service.readback(validation_run_id))
            payload["budget"] = budget.snapshot()
            payload["budget_estimate"] = estimate
            payload["post_canary_projection"] = projection
            payload["content_hash"] = _payload_hash(payload)

            output_dir.mkdir(parents=True, exist_ok=True)
            short_id = validation_run_id.replace("trader-demo-", "")[:8]
            final_path = output_dir / f"ai_trader_full_test_20260710_{short_id}.xlsx"
            if final_path.exists():
                raise FileExistsError(f"OUTPUT_EXISTS:{final_path.name}")
            temp_path = output_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp.xlsx"
            _build_excel(payload, temp_path, output_dir)
            validation = _validate_xlsx(temp_path, payload)
            os.replace(temp_path, final_path)
            workbook_hash = hashlib.sha256(final_path.read_bytes()).hexdigest()
            sidecar = _sidecar(payload, validation, final_path, workbook_hash)
            sidecar["budget"] = budget.snapshot()
            sidecar_text = json.dumps(sidecar, ensure_ascii=False, indent=2, default=str)
            if not _secret_scan(sidecar_text):
                raise ValueError("SIDECAR_SECRET_SCAN_FAILED")
            audit_path = final_path.with_name(final_path.stem + "_audit.json")
            audit_path.write_text(sidecar_text, encoding="utf-8")
            report.update({
                "status": "SUCCESS",
                "validation_run_id": validation_run_id,
                "budget_actual": budget.snapshot(),
                "excel_output": str(final_path),
                "excel_audit": str(audit_path),
                "excel_validation": validation,
                "workbook_sha256": workbook_hash,
                "row_counts": payload["row_counts"],
            })

        report["runtime_switch_restoration"] = _runtime_switch_status()
        if not report["runtime_switch_restoration"]["restored"]:
            raise RuntimeError("REAL_LLM_RUNTIME_SWITCH_RESTORE_FAILED")
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_report(report)
        print(json.dumps(_public_report(report), ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        report["status"] = "STOPPED"
        report["problems"].append(_redacted_error(exc))
        report["runtime_switch_restoration"] = _runtime_switch_status()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_report(report)
        print(json.dumps(_public_report(report), ensure_ascii=False, indent=2, default=str))
        return 2
    finally:
        if session is not None:
            session.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded 2026-07-10 full daily model-validation pipeline")
    parser.add_argument("--required-base-date", default="2026-07-10")
    parser.add_argument("--run-mode", default="POST_MARKET_FINAL")
    parser.add_argument("--quant-top-n", type=int, default=100)
    parser.add_argument("--llm-select-n", type=int, default=20)
    parser.add_argument("--manual-stocks", default=",".join(code for _, code in MANUAL_STOCKS))
    parser.add_argument("--flash-model-alias", default="light-screening-default")
    parser.add_argument("--pro-model-alias", default="controller-high-capability")
    parser.add_argument("--fundamental-knowledge-mode", default="LLM_UNVERIFIED_CURRENT")
    parser.add_argument("--daily-token-budget", type=int, default=5_000_000)
    parser.add_argument("--token-warning-percent", type=int, default=80)
    parser.add_argument("--flash-token-budget", type=int, default=3_500_000)
    parser.add_argument("--pro-token-budget", type=int, default=800_000)
    parser.add_argument("--repair-token-budget", type=int, default=400_000)
    parser.add_argument("--final-token-reserve", type=int, default=300_000)
    parser.add_argument("--real-llm", action="store_true")
    parser.add_argument("--confirm-budget-warning", action="store_true")
    parser.add_argument("--canary-size", type=int, default=5)
    parser.add_argument("--reestimate-after-canary", action="store_true")
    parser.add_argument("--stop-on-projected-hard-limit", action="store_true")
    parser.add_argument("--continue-on-stock-error", nargs="?", const=True, type=_bool_arg, default=True)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--account-equity", default="1000000")
    parser.add_argument("--available-cash", default="1000000")
    parser.add_argument(
        "--output-dir",
        default=f"outputs/{datetime.now().date().isoformat()}/历史版本/完整流水线",
    )
    parser.add_argument("--stop-if-not-ready", action="store_true")
    parser.add_argument("--resume-from-checkpoint", default="")
    parser.add_argument("--resume-stage", default="")
    parser.add_argument("--reuse-flash-results", action="store_true")
    parser.add_argument("--new-pro-contract", default="")
    parser.add_argument("--pro-chunk-size", type=int, default=5)
    parser.add_argument("--pro-candidate-mode", default="")
    parser.add_argument("--pro-candidate-thinking", default="")
    parser.add_argument("--pro-candidate-max-tokens", type=int, default=1800)
    parser.add_argument("--pro-portfolio-thinking", default="")
    parser.add_argument("--pro-portfolio-max-tokens", type=int, default=3200)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    expected = [code for _, code in MANUAL_STOCKS]
    actual = [normalize_ts_code(item) for item in args.manual_stocks.split(",") if item.strip()]
    required = {
        "required_base_date": "2026-07-10", "run_mode": "POST_MARKET_FINAL",
        "quant_top_n": 100, "llm_select_n": 20, "flash_model_alias": "light-screening-default",
        "pro_model_alias": "controller-high-capability", "fundamental_knowledge_mode": "LLM_UNVERIFIED_CURRENT",
        "canary_size": 5, "concurrency": 5, "batch_size": 10,
    }
    for key, value in required.items():
        if getattr(args, key) != value:
            raise ValueError(f"FULL_PIPELINE_ARGUMENT_MISMATCH:{key}")
    if actual != expected:
        raise ValueError("MANUAL_STOCK_SET_MISMATCH")
    if Decimal(args.account_equity) != Decimal("1000000") or Decimal(args.available_cash) != Decimal("1000000"):
        raise ValueError("ISOLATED_ACCOUNT_ARGUMENT_MISMATCH")
    if not (args.reestimate_after_canary and args.stop_on_projected_hard_limit and args.stop_if_not_ready):
        raise ValueError("REQUIRED_STOP_GUARDS_NOT_ENABLED")


def _validate_baseline(session, service: TraderDemoService, args: argparse.Namespace) -> dict[str, Any]:
    run, manifest, rows = service.load_context(QUANT_RUN_ID, LLMKnowledgeMode.LLM_UNVERIFIED_CURRENT)
    expected_run = {
        "run_mode": args.run_mode,
        "base_market_trade_date": args.required_base_date,
        "target_trade_date": "2026-07-13",
        "status": "COMPLETED", "temporal_status": "PASS", "scored_count": 5308,
        "no_llm_call_verified": True, "per_stock_api_call_count": 0,
    }
    for field, expected in expected_run.items():
        actual = getattr(run, field)
        if str(actual) != str(expected):
            raise ValueError(f"QUANT_BASELINE_MISMATCH:{field}:{actual}")
    if not run.actionable or not manifest.actionable or manifest.temporal_status != "PASS":
        raise ValueError("TEMPORAL_GATE_NOT_ACTIONABLE")
    top100 = [row for row in rows if row.rank <= 100]
    if len(top100) != 100:
        raise ValueError("QUANT_TOP100_INCOMPLETE")

    resolution = ManualStockResolver(session).resolve_all([name for name, _ in MANUAL_STOCKS])
    rank_by_code = {normalize_ts_code(row.stock_code): row.rank for row in rows}
    manual_rows = []
    for item, (_, expected_code) in zip(resolution, MANUAL_STOCKS):
        if item.ts_code != expected_code or item.ts_code not in rank_by_code:
            raise ValueError(f"MANUAL_STOCK_RESOLUTION_MISMATCH:{item.input_name}")
        manual_rows.append({**item.as_dict(), "quant_rank": rank_by_code[item.ts_code], "in_top100": rank_by_code[item.ts_code] <= 100})
    manual_selections = [ManualSelection(item.ts_code) for item in resolution]
    pool_rows = service.evaluation_rows(rows, ranks=None, top_n=100, manual=manual_selections)
    if len(pool_rows) != 105:
        raise ValueError(f"FLASH_POOL_SIZE_MISMATCH:{len(pool_rows)}")

    report = json.loads(QUANT_REPORT.read_text(encoding="utf-8"))
    checks = {
        "requested_trade_date": "2026-07-10", "scored_count": 5308, "top_count": 100,
        "no_llm_call_verified": True, "per_stock_api_call_count": 0,
        "price_adjustment_mode": "RAW", "price_limit_risk_enabled": False,
    }
    for key, expected in checks.items():
        if report.get(key) != expected:
            raise ValueError(f"QUANT_REPORT_MISMATCH:{key}:{report.get(key)}")

    manager = ConfigManager(session=session)
    if manager.get_config_value("llm.budgets.daily_token_budget") != 5_000_000:
        raise ValueError("ACTIVE_DATABASE_TOKEN_BUDGET_NOT_5M")
    db_record = session.scalar(select(SystemConfig).where(SystemConfig.config_key == "llm.budgets.daily_token_budget"))
    history = session.scalar(
        select(ConfigHistory)
        .where(ConfigHistory.config_key == "llm.budgets.daily_token_budget", ConfigHistory.reason == CONFIG_REASON)
        .order_by(ConfigHistory.id.desc())
    )
    if db_record is None or history is None:
        raise ValueError("TOKEN_BUDGET_CONFIG_HISTORY_MISSING")
    return {
        "baseline": {
            "stock_master_count": int(session.scalar(select(func.count()).select_from(StockMaster)) or 0),
            "temporal_gate": manifest.temporal_status, "temporal_actionable": manifest.actionable,
            "quant_report": str(QUANT_REPORT.relative_to(ROOT_DIR)),
        },
        "quant_run": {field: str(getattr(run, field)) for field in expected_run},
        "manual_stocks": manual_rows,
        "pool_count": len(pool_rows),
        "pool_rows": pool_rows,
        "token_configuration": {
            "old_daily_limit": 1_800_000, "new_daily_limit": 5_000_000,
            "warning_threshold": 4_000_000, "hard_threshold": 5_000_000,
            "configuration_source": "Database",
            "config_history_id": history.id,
            "reason": history.reason,
        },
    }


def _initial_budget_estimate(service: TraderDemoService, rows: list[QuantRankResult], config: PipelineBudgetConfig) -> dict[str, int]:
    run, manifest, _ = service.load_context(QUANT_RUN_ID, LLMKnowledgeMode.LLM_UNVERIFIED_CURRENT)
    estimated_input = 0
    for row in rows:
        profile = service.guard._profile(row.stock_code, run)
        context = service.guard._structured_context(run, manifest, row, profile)
        estimated_input += max(1, len(json.dumps(context, ensure_ascii=False, default=str)) // 4)
        estimated_input += 1_200
    expected_flash = estimated_input + len(rows) * (900 + 350)
    reserved_flash = estimated_input + len(rows) * (3_200 + 1_400)
    worst_flash = reserved_flash * 2
    reserved_pro = 80_000
    reserved_repair = min(config.repair_limit, reserved_flash // 10)
    return {
        "expected_tokens": expected_flash + 40_000,
        "reserved_tokens": reserved_flash + reserved_pro + reserved_repair,
        "worst_case_tokens": worst_flash + 160_000 + config.repair_limit,
        "reserved_flash_tokens": reserved_flash,
        "reserved_pro_tokens": reserved_pro,
        "reserved_repair_tokens": reserved_repair,
        "final_safety_reserve": config.final_reserve,
    }


def _validate_canary(session, run_id: str) -> dict[str, Any]:
    run = session.scalar(select(ModelValidationRun).where(ModelValidationRun.run_id == run_id))
    samples = list(session.scalars(select(ModelValidationSample).where(ModelValidationSample.validation_run_id == run_id)))
    audits = list(session.scalars(select(ModelValidationLLMAudit).where(ModelValidationLLMAudit.validation_run_id == run_id)))
    if run is None or run.status != "SUCCESS" or len(samples) != 5 or len(audits) != 10:
        raise ValueError("CANARY_INCOMPLETE")
    if any(row.status not in {"ok", "SUCCESS"} or row.schema_status != "PASS" for row in audits):
        raise ValueError("CANARY_TASK_FAILED")
    if any(row.actual_model != "deepseek-v4-flash" for row in audits):
        raise ValueError("CANARY_ACTUAL_MODEL_MISMATCH")
    required = (
        "industry_chain", "level_one_sector_explanation", "main_business_summary",
        "industry_position", "core_products", "competitive_advantage", "industry_trend",
        "investment_logic", "invalidation_conditions", "domestic_substitution",
        "observation_rating", "financial_status",
    )
    for sample in samples:
        fundamental = sample.fundamental_result or {}
        if any(fundamental.get(key) in (None, "", [], {}) for key in required):
            raise ValueError(f"CANARY_FUNDAMENTAL_FIELD_EMPTY:{sample.stock_code}")
        text = json.dumps({"fundamental": fundamental, "screening": sample.screening_result}, ensure_ascii=False)
        if any(marker in text.lower() for marker in ("http://", "https://", "www.")):
            raise ValueError(f"CANARY_URL_BOUNDARY_FAILED:{sample.stock_code}")
    task_usages = []
    reused_totals = {"input": 0, "output": 0, "repair_input": 0, "repair_output": 0}
    for row in audits:
        effective = row
        visited: set[str] = set()
        while effective.cache_status == "REUSED":
            source_run = str((effective.diagnostics or {}).get("source_validation_run_id") or "")
            if not source_run or source_run in visited:
                raise ValueError(f"CANARY_REUSED_AUDIT_CHAIN_INVALID:{row.stock_code}:{row.task}")
            visited.add(source_run)
            effective = session.scalar(
                select(ModelValidationLLMAudit)
                .where(
                    ModelValidationLLMAudit.validation_run_id == source_run,
                    ModelValidationLLMAudit.stock_code == row.stock_code,
                    ModelValidationLLMAudit.task == row.task,
                    ModelValidationLLMAudit.schema_status == "PASS",
                )
                .order_by(ModelValidationLLMAudit.id.desc())
            )
            if effective is None:
                raise ValueError(f"CANARY_REUSED_AUDIT_SOURCE_MISSING:{row.stock_code}:{row.task}")
        usage = {
            "input_tokens": effective.input_tokens,
            "output_tokens": effective.output_tokens,
            "repair_input_tokens": int((effective.diagnostics or {}).get("repair_input_tokens") or 0),
            "repair_output_tokens": int((effective.diagnostics or {}).get("repair_output_tokens") or 0),
            "latency_ms": effective.latency_ms,
            "cost_usd": float(effective.cost_usd or 0),
        }
        task_usages.append(usage)
        if row.cache_status == "REUSED":
            reused_totals["input"] += usage["input_tokens"]
            reused_totals["output"] += usage["output_tokens"]
            reused_totals["repair_input"] += usage["repair_input_tokens"]
            reused_totals["repair_output"] += usage["repair_output_tokens"]
    return {
        "status": "PASS", "validation_run_id": run_id, "stock_count": len(samples),
        "task_count": len(audits), "actual_model": "deepseek-v4-flash",
        "task_usages": task_usages,
        "reused_input_tokens": reused_totals["input"],
        "reused_output_tokens": reused_totals["output"],
        "reused_repair_input_tokens": reused_totals["repair_input"],
        "reused_repair_output_tokens": reused_totals["repair_output"],
    }


def _post_canary_projection(usages: list[dict[str, Any]], *, total_flash_tasks: int, connectivity_tokens: int) -> dict[str, Any]:
    inputs = [int(item["input_tokens"]) - int(item["repair_input_tokens"]) for item in usages]
    outputs = [int(item["output_tokens"]) - int(item["repair_output_tokens"]) for item in usages]
    projected = canary_projection(inputs, outputs, total_task_count=total_flash_tasks, repair_probability=0)
    observed_repair = sum(int(item["repair_input_tokens"]) + int(item["repair_output_tokens"]) for item in usages)
    repair = max(int(projected["reserved_tokens"] * 0.05), observed_repair * total_flash_tasks // max(1, len(usages)))
    pro_input = 70_000
    pro_output = 10_000
    flash_total = int(projected["reserved_tokens"])
    grand_total = connectivity_tokens + flash_total + pro_input + pro_output + repair
    return {
        **projected,
        "projected_flash_total": flash_total,
        "projected_pro_input": pro_input,
        "projected_pro_output": pro_output,
        "projected_pro_total": pro_input + pro_output,
        "projected_repair_total": repair,
        "projected_grand_total": grand_total,
    }


def _validate_full_flash(session, run_id: str, selected_n: int) -> dict[str, Any]:
    run = session.scalar(select(ModelValidationRun).where(ModelValidationRun.run_id == run_id))
    samples = list(session.scalars(select(ModelValidationSample).where(ModelValidationSample.validation_run_id == run_id)))
    if run is None or run.status not in {"SUCCESS", "PARTIAL_SUCCESS"} or len(samples) != 105:
        raise ValueError("FLASH_FULL_BATCH_INCOMPLETE")
    selected = [sample for sample in samples if (sample.screening_result or {}).get("_trader_demo", {}).get("llm_selected")]
    manual = [sample for sample in samples if (sample.screening_result or {}).get("_trader_demo", {}).get("manual_selected")]
    candidates = [sample for sample in samples if (sample.screening_result or {}).get("_trader_demo", {}).get("selection_source")]
    if len(selected) != selected_n:
        raise ValueError(f"MODEL_VALIDATION_TOP20_COUNT_MISMATCH:{len(selected)}")
    expected_manual = {code for _, code in MANUAL_STOCKS}
    if {normalize_ts_code(sample.stock_code) for sample in manual} != expected_manual:
        raise ValueError("MANUAL_POOL_NOT_PRESERVED")
    return {
        "status": run.status, "validation_run_id": run_id, "evaluation_count": len(samples),
        "success_count": sum((sample.screening_result or {}).get("_trader_demo", {}).get("execution_status") == "SUCCESS" for sample in samples),
        "failure_count": sum((sample.screening_result or {}).get("_trader_demo", {}).get("execution_status") != "SUCCESS" for sample in samples),
        "top20_count": len(selected), "manual_count": len(manual), "candidate_count": len(candidates),
        "top20": [normalize_ts_code(sample.stock_code) for sample in selected],
    }


def _find_resumable_full_run(session) -> str | None:
    runs = session.scalars(
        select(ModelValidationRun)
        .where(
            ModelValidationRun.quant_run_id == QUANT_RUN_ID,
            ModelValidationRun.knowledge_mode == LLMKnowledgeMode.LLM_UNVERIFIED_CURRENT.value,
            ModelValidationRun.status.in_(["SUCCESS", "PARTIAL_SUCCESS"]),
        )
        .order_by(ModelValidationRun.id.desc())
    )
    for run in runs:
        sample_count = int(session.scalar(
            select(func.count()).select_from(ModelValidationSample)
            .where(ModelValidationSample.validation_run_id == run.run_id)
        ) or 0)
        audit_count = int(session.scalar(
            select(func.count()).select_from(ModelValidationLLMAudit)
            .where(
                ModelValidationLLMAudit.validation_run_id == run.run_id,
                ModelValidationLLMAudit.task.in_([
                    "fundamental_structured_inference", "structured_light_screening"
                ]),
            )
        ) or 0)
        pro_count = int(session.scalar(
            select(func.count()).select_from(ModelValidationLLMAudit)
            .where(
                ModelValidationLLMAudit.validation_run_id == run.run_id,
                ModelValidationLLMAudit.task == "daily_pro_aggregation",
            )
        ) or 0)
        if sample_count == 105 and audit_count == 210 and pro_count == 0:
            return run.run_id
    return None


def _existing_flash_usage(session, run_id: str) -> dict[str, int]:
    rows = list(session.scalars(
        select(ModelValidationLLMAudit).where(
            ModelValidationLLMAudit.validation_run_id == run_id,
            ModelValidationLLMAudit.task.in_([
                "fundamental_structured_inference", "structured_light_screening"
            ]),
            ModelValidationLLMAudit.cache_status != "REUSED",
        )
    ))
    return {
        "input_tokens": sum(row.input_tokens for row in rows),
        "output_tokens": sum(row.output_tokens for row in rows),
        "repair_input_tokens": sum(int((row.diagnostics or {}).get("repair_input_tokens") or 0) for row in rows),
        "repair_output_tokens": sum(int((row.diagnostics or {}).get("repair_output_tokens") or 0) for row in rows),
    }


def _score_snapshot(samples: list[ModelValidationSample]) -> dict[str, tuple[Any, Any]]:
    return {
        normalize_ts_code(sample.stock_code): (
            (sample.quant_scores or {}).get("total_score"),
            (sample.screening_result or {}).get("llm_score"),
        )
        for sample in samples
    }


def _payload_hash(payload: dict[str, Any]) -> str:
    material = {key: value for key, value in payload.items() if key != "content_hash"}
    text = json.dumps(material, ensure_ascii=False, sort_keys=True, default=str)
    if not _secret_scan(text):
        raise ValueError("FULL_PIPELINE_PAYLOAD_SECRET_SCAN_FAILED")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _runtime_switch_status() -> dict[str, Any]:
    values = {
        "LLM_REAL_CALLS_ENABLED": os.getenv("LLM_REAL_CALLS_ENABLED", "false").lower(),
        "RUN_REAL_FUNDAMENTAL_RESEARCH": os.getenv("RUN_REAL_FUNDAMENTAL_RESEARCH", "false").lower(),
        "LLM_GATEWAY_MOCK_ONLY": os.getenv("LLM_GATEWAY_MOCK_ONLY", "true").lower(),
    }
    restored = values["LLM_REAL_CALLS_ENABLED"] not in {"1", "true", "yes", "on"}
    restored = restored and values["RUN_REAL_FUNDAMENTAL_RESEARCH"] not in {"1", "true", "yes", "on"}
    restored = restored and values["LLM_GATEWAY_MOCK_ONLY"] not in {"0", "false", "no", "off"}
    return {"restored": restored, **values}


def _prior_connectivity_tokens() -> int:
    if not REPORT_PATH.exists():
        return 0
    try:
        prior = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return 0
    if prior.get("status") not in {"STOPPED", "ERROR"}:
        return 0
    carried = int((prior.get("token_carryover") or {}).get("connectivity") or 0)
    if carried:
        return carried
    return int((((prior.get("connectivity") or {}).get("connectivity") or {}).get("token_usage") or {}).get("total_tokens") or 0)


def _write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    safe = _public_report(report)
    REPORT_PATH.write_text(json.dumps(safe, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _public_report(report: dict[str, Any]) -> dict[str, Any]:
    public = dict(report)
    public.pop("pool_rows", None)
    text = json.dumps(public, ensure_ascii=False, default=str)
    if not _secret_scan(text):
        raise ValueError("REPORT_SECRET_SCAN_FAILED")
    return public


def _redacted_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}:{exc}"
    for name in ("DEEPSEEK_API_KEY", "TUSHARE_TOKEN", "DATABASE_URL"):
        value = os.getenv(name, "").strip()
        if value:
            text = text.replace(value, "[REDACTED]")
    return text[:500]


def _resume_main(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "phase": "Pro Aggregation Contract Simplification + Checkpoint Resume",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": [], "problems": [], "stopped_stages": [],
    }
    session = None
    resume_run: ProResumeRun | None = None
    ledger: AuthoritativeUsageLedger | None = None
    try:
        _validate_resume_args(args)
        init_db()
        session = get_session()
        context = load_resume_context(session)
        checkpoint_path = (ROOT_DIR / args.resume_from_checkpoint).resolve()
        checkpoint = validate_or_upgrade_checkpoint(
            checkpoint_path, context, chunk_size=args.pro_chunk_size
        )
        chunks = chunk_candidates(context.candidates, args.pro_chunk_size)
        ledger = AuthoritativeUsageLedger(session.get_bind())
        ledger_before = ledger.backfill_flash_and_legacy(
            session,
            flash_validation_run_id=context.flash_run.run_id,
            connectivity_total_tokens=288,
        )
        canary_projection = _historical_canary_projection(session)
        _repair_legacy_report(ledger_before, canary_projection)
        estimate = _resume_pro_estimate(context, chunks)
        if estimate["reserved_pro_tokens"] > 800_000:
            raise PipelineBudgetExceeded("PROJECTED_PRO_TOKEN_BUDGET_EXCEEDED")
        known_total = int(ledger_before["total_actual_api_tokens"])
        if known_total + estimate["reserved_pro_tokens"] > 4_700_000:
            raise PipelineBudgetExceeded("PROJECTED_TOTAL_TOKEN_SAFETY_LIMIT_EXCEEDED")
        report.update({
            "baseline": {
                "quant_run_id": context.quant_run.run_id,
                "manifest_id": context.manifest.manifest_id,
                "flash_validation_run_id": context.flash_run.run_id,
                "base_trade_date": context.quant_run.base_market_trade_date.isoformat(),
                "target_trade_date": context.quant_run.target_trade_date.isoformat(),
                "quant_scored": context.quant_run.scored_count,
                "quant_no_llm": context.quant_run.no_llm_call_verified,
                "per_stock_api_call_count": context.quant_run.per_stock_api_call_count,
            },
            "checkpoint_validation": {
                "status": "PASS", "path": str(checkpoint_path),
                "top20_count": len(context.top20_codes), "manual_count": len(context.manual_codes),
                "candidate_count": len(context.candidate_codes),
                "top20_hash": context.top20_hash, "manual_hash": context.manual_hash,
                "candidate_set_hash": context.candidate_set_hash,
                "quant_rerun": False, "flash_rerun": False,
            },
            "legacy_pro_failure_diagnosis": _legacy_pro_diagnosis(session, context.flash_run.run_id),
            "report_consistency_fixes": {
                "stopped_stages_populated": True, "pro_aggregation_object_populated": True,
                "canary_usage_source": canary_projection["usage_source"],
                "post_canary_projection_nonzero": canary_projection["projected_flash_total"] > 0,
            },
            "canary_projection": canary_projection,
            "authoritative_token_ledger_before": ledger_before,
            "pro_contract_v2": {
                "contract_version": PRO_CONTRACT_VERSION,
                "prompt_version": PRO_CANDIDATE_PROMPT_VERSION,
                "portfolio_prompt_version": PRO_PORTFOLIO_PROMPT_VERSION,
                "chunk_size": args.pro_chunk_size, "chunk_count": len(chunks),
            },
            "pro_chunks_planned": [
                {
                    "chunk_id": f"candidate-{index:02d}",
                    "stock_count": len(chunk),
                    "stock_codes": [normalize_ts_code(sample.stock_code) for sample in chunk],
                }
                for index, chunk in enumerate(chunks, start=1)
            ],
            "budget_estimate": estimate,
            "real_gate_status": {
                "deepseek_key_configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
                "real_calls_currently_enabled": _env_true("LLM_REAL_CALLS_ENABLED"),
                "fundamental_research_currently_enabled": _env_true("RUN_REAL_FUNDAMENTAL_RESEARCH"),
                "gateway_default_mock_only": not os.getenv("LLM_GATEWAY_MOCK_ONLY", "true").lower() in {"0", "false", "no", "off"},
            },
        })
        if args.dry_run:
            report["status"] = "DRY_RUN_PASS"
            report["completed"].append("Checkpoint and candidate hashes validated without Quant, Flash, or LLM calls")
            _write_resume_report(report)
            print(json.dumps(_public_report(report), ensure_ascii=False, indent=2, default=str))
            return 0
        if not args.real_llm:
            raise ValueError("RESUME_REAL_LLM_CONFIRMATION_REQUIRED")

        resume_run = _get_or_create_pro_resume_run(session, context, args.pro_chunk_size)
        report["pro_resume_run_id"] = resume_run.run_id
        flash_audit_count_before = _flash_audit_count(session, context.flash_run.run_id)
        with temporary_real_llm_runtime():
            pro_service = ProResumeService(session, ledger)
            chunk_reports = pro_service.run_candidate_chunks(
                resume_run, context.candidates,
                selection_sources=context.selection_sources, chunks=chunks,
                model_alias=args.pro_model_alias,
            )
            reviews = pro_service.rank_locally_and_apply(
                resume_run, context.candidates, context.selection_sources
            )
            portfolio, portfolio_report = pro_service.run_portfolio_summary(
                resume_run, context.candidates, reviews,
                selection_sources=context.selection_sources,
                model_alias=args.pro_model_alias,
            )
            report["pro_chunks"] = chunk_reports
            report["local_pro_ranking"] = {
                "status": "PASS", "count": len(reviews), "generated_by": "LOCAL_DETERMINISTIC_CODE",
                "top_codes": [row.stock_code for row in sorted(reviews, key=lambda item: int(item.pro_rank or 999))[:10]],
            }
            report["portfolio_summary"] = {
                "status": "PASS", "report": portfolio_report,
                "portfolio_risk_level": portfolio.portfolio_risk_level,
                "top_priority_stock_codes": portfolio.top_priority_stock_codes,
            }

        runtime = _runtime_switch_status()
        if not runtime["restored"]:
            raise RuntimeError("REAL_LLM_RUNTIME_SWITCH_RESTORE_FAILED")
        report["runtime_switch_restoration"] = runtime
        if _flash_audit_count(session, context.flash_run.run_id) != flash_audit_count_before:
            raise ValueError("FLASH_AUDIT_COUNT_CHANGED_DURING_RESUME")
        ledger_after_pro = ledger.summary(pro_resume_run_id=resume_run.run_id)
        if int(ledger_after_pro["current_resume_new_tokens"]) >= 800_000:
            raise PipelineBudgetExceeded("PRO_TOKEN_BUDGET_EXCEEDED")
        if int(ledger_after_pro["total_actual_api_tokens"]) >= 4_700_000:
            raise PipelineBudgetExceeded("TOTAL_TOKEN_SAFETY_RESERVE_REACHED")
        report["authoritative_token_ledger"] = {
            **ledger_after_pro,
            "remaining_budget": 5_000_000 - int(ledger_after_pro["total_actual_api_tokens"]),
        }

        trader_service = TraderDemoService(session)
        plan_count = session.scalar(select(func.count()).select_from(ModelValidationOrderPlan).where(
            ModelValidationOrderPlan.validation_run_id == context.flash_run.run_id
        )) or 0
        if plan_count == 0:
            trader_service.generate_candidate_outputs(
                context.flash_run.run_id,
                account_equity=Decimal(args.account_equity),
                available_cash=Decimal(args.available_cash),
            )
        readback = trader_service.readback(context.flash_run.run_id)
        payload = _serialize_readback(readback)
        _validate_resume_payload(payload)
        payload["budget"] = _excel_budget_payload(ledger_after_pro)
        payload["pro_resume"] = {
            "run_id": resume_run.run_id, "contract_version": resume_run.pro_contract_version,
            "candidate_set_hash": resume_run.candidate_set_hash,
            "portfolio_result": resume_run.portfolio_result,
        }
        payload["content_hash"] = _payload_hash(payload)
        output_dir = (ROOT_DIR / args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        short_id = resume_run.run_id.replace("pro-resume-", "")[:8]
        final_path = output_dir / f"ai_trader_full_test_20260710_{short_id}.xlsx"
        if final_path.exists():
            raise FileExistsError(f"OUTPUT_EXISTS:{final_path.name}")
        temp_path = output_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp.xlsx"
        _build_excel(payload, temp_path, output_dir)
        validation = _validate_xlsx(temp_path, payload)
        os.replace(temp_path, final_path)
        workbook_hash = hashlib.sha256(final_path.read_bytes()).hexdigest()
        sidecar = _sidecar(payload, validation, final_path, workbook_hash)
        sidecar.update({
            "pro_resume": payload["pro_resume"],
            "authoritative_token_ledger": report["authoritative_token_ledger"],
        })
        sidecar_text = json.dumps(sidecar, ensure_ascii=False, indent=2, default=str)
        if not _secret_scan(sidecar_text):
            raise ValueError("SIDECAR_SECRET_SCAN_FAILED")
        audit_path = final_path.with_name(final_path.stem + "_audit.json")
        audit_path.write_text(sidecar_text, encoding="utf-8")
        resume_run.status = "COMPLETED"
        session.commit()
        report.update({
            "status": "SUCCESS",
            "order_plans": {"count": len(payload["order_rows"]), "source": "RULE_ENGINE", "actionable": False},
            "position_sizing": {"count": len(payload["order_rows"]), "source": "RULE_ENGINE", "actionable": False},
            "fundamental_completeness": {"status": "PASS", "count": len(payload["fundamental_rows"])},
            "excel_output": str(final_path), "excel_audit": str(audit_path),
            "excel_validation": validation, "workbook_sha256": workbook_hash,
            "workbook_row_counts": {
                "quant": len(payload["quant_rows"]), "flash": len(payload["llm_rows"]),
                "top20": payload["row_counts"]["llm_selected_count"],
                "manual": payload["row_counts"]["manual_selected_count"],
                "candidate": len(payload["order_rows"]), "order": len(payload["order_rows"]),
                "position": len(payload["order_rows"]), "fundamental": len(payload["fundamental_rows"]),
            },
            "quant_no_llm_regression": {
                "verified": context.quant_run.no_llm_call_verified,
                "per_stock_api_call_count": context.quant_run.per_stock_api_call_count,
                "flash_calls_repeated": 0,
            },
        })
        report["completed"].extend([
            "Six Pro candidate chunks", "Local deterministic Pro ranking",
            "Portfolio summary", "Rule order plans", "Rule position sizing", "Five-sheet Excel",
        ])
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_resume_report(report)
        print(json.dumps(_public_report(report), ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        if resume_run is not None and session is not None:
            resume_run.status = "PARTIAL_PRO_FAILURE"
            session.commit()
        report["status"] = "STOPPED"
        report["problems"].append(_redacted_error(exc))
        report["stopped_stages"] = ["PRO_AGGREGATION", "ORDER_PLAN", "POSITION_SIZING", "EXCEL_EXPORT"]
        if isinstance(exc, ProStageFailure):
            report["pro_aggregation"] = {
                "status": "FAILED", "run_id": resume_run.run_id if resume_run else "",
                "contract_version": PRO_CONTRACT_VERSION,
                "prompt_version": PRO_CANDIDATE_PROMPT_VERSION,
                "input_stock_count": 26,
                "stage_report": exc.stage_report,
                "error_category": exc.category,
            }
        if ledger is not None and resume_run is not None:
            ledger_after_failure = ledger.summary(pro_resume_run_id=resume_run.run_id)
            report["authoritative_token_ledger"] = {
                **ledger_after_failure,
                "remaining_budget": 5_000_000 - int(ledger_after_failure["total_actual_api_tokens"]),
            }
            report["pro_chunk_failures"] = [
                report.get("pro_aggregation", {}).get("stage_report", {})
            ]
        report["runtime_switch_restoration"] = _runtime_switch_status()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_resume_report(report)
        print(json.dumps(_public_report(report), ensure_ascii=False, indent=2, default=str))
        return 2
    finally:
        if session is not None:
            session.close()


def _resume_v3_main(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "phase": "Single-Stock Pro Review V3 + Deterministic Ranking + Pipeline Completion",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": [], "problems": [], "stopped_stages": [],
    }
    session = None
    resume_run: ProResumeRun | None = None
    ledger: AuthoritativeUsageLedger | None = None
    started = datetime.now(timezone.utc)
    try:
        _validate_v3_args(args)
        init_db()
        session = get_session()
        ensure_v3_schema(session.get_bind())
        context = load_resume_context(session)
        if context.candidate_set_hash != "6f577ccf591a59564a2377df03dbb01058fc3f42555cadd3ab0ae6d069429004":
            raise ValueError("CHECKPOINT_CANDIDATE_HASH_MISMATCH")
        checkpoint_path = (ROOT_DIR / args.resume_from_checkpoint).resolve()
        validate_or_upgrade_checkpoint(checkpoint_path, context, chunk_size=5)
        ordered = stable_v3_order(context.candidates)
        canary = select_v3_canary(ordered)
        ledger = AuthoritativeUsageLedger(session.get_bind())
        ledger_before = ledger.backfill_flash_and_legacy(
            session, flash_validation_run_id=context.flash_run.run_id,
            connectivity_total_tokens=288,
        )
        previous_v2 = ledger.summary(pro_resume_run_id=PREVIOUS_FAILED_RUN_ID)
        estimate = _v3_estimate(ordered, context.selection_sources)
        config = ConfigManager().get_llm_gateway_config().get("pro_resume_v3", {})
        report.update({
            "baseline": {
                "quant_run_id": context.quant_run.run_id,
                "manifest_id": context.manifest.manifest_id,
                "flash_validation_run_id": context.flash_run.run_id,
                "quant_scored": 5308, "flash_evaluation": 105,
                "flash_success": 104, "flash_failure": 1,
                "top20": 20, "manual": 7, "candidate": 26,
            },
            "checkpoint_validation": {
                "status": "PASS", "candidate_set_hash": context.candidate_set_hash,
                "top20_hash": context.top20_hash, "manual_hash": context.manual_hash,
                "quant_rerun": False, "flash_rerun": False,
            },
            "previous_pro_failure": {
                "run_id": PREVIOUS_FAILED_RUN_ID, "status": "PARTIAL_PRO_FAILURE",
                "reason": "thinking completion exhausted max_tokens=1800; repair JSON truncated",
                "preserved": True, "previous_v2_actual_tokens": previous_v2["current_resume_new_tokens"],
            },
            "pro_configuration": {
                "actual_model": "deepseek-v4-pro",
                "candidate_thinking_mode": str(config.get("candidate_thinking") or "disabled"),
                "candidate_max_tokens": int(config.get("candidate_max_tokens") or 1800),
                "portfolio_thinking_mode": str(config.get("portfolio_thinking") or "disabled"),
                "portfolio_max_tokens": int(config.get("portfolio_max_tokens") or 3200),
                "candidate_contract_version": SINGLE_CONTRACT_VERSION,
                "portfolio_contract_version": PORTFOLIO_CONTRACT_VERSION,
                "candidate_mode": "single-stock", "concurrency": 3,
            },
            "pro_canary_planned": [normalize_ts_code(sample.stock_code) for sample in canary],
            "single_stock_order": [normalize_ts_code(sample.stock_code) for sample in ordered],
            "candidate_call_count": 26, "portfolio_call_count": 1,
            "budget_estimate": estimate,
            "token_ledger_before": {
                **ledger_before,
                "previous_pro_v2_actual": previous_v2["current_resume_new_tokens"],
                "remaining_budget_from_known_actual": 5_000_000 - int(ledger_before["total_actual_api_tokens"]),
            },
            "runtime_real_gate_status": {
                "deepseek_key_configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
                "real_calls_enabled": _env_true("LLM_REAL_CALLS_ENABLED"),
                "gateway_default_mock_only": not os.getenv("LLM_GATEWAY_MOCK_ONLY", "true").lower() in {"0", "false", "no", "off"},
            },
        })
        if estimate["reserved_total_tokens"] > 580_000:
            raise PipelineBudgetExceeded("PRO_V3_RESERVED_BUDGET_EXCEEDED")
        if int(ledger_before["total_actual_api_tokens"]) + estimate["reserved_total_tokens"] >= 4_700_000:
            raise PipelineBudgetExceeded("PROJECTED_TOTAL_TOKEN_SAFETY_LIMIT_EXCEEDED")
        if args.dry_run:
            report["status"] = "DRY_RUN_PASS"
            report["completed"].append("Validated V3 single-stock plan without Quant, Flash, or LLM calls")
            _write_v3_report(report)
            print(json.dumps(_public_report(report), ensure_ascii=False, indent=2, default=str))
            return 0
        if not args.real_llm:
            raise ValueError("RESUME_REAL_LLM_CONFIRMATION_REQUIRED")

        resume_run = _create_v3_resume_run(session, context)
        prior_snapshot = dict(resume_run.config_snapshot or {})
        prior_candidate_reports = [
            *list(prior_snapshot.get("canary_reports") or []),
            *list(prior_snapshot.get("full_reports") or []),
        ]
        report["new_pro_resume_run"] = {
            "run_id": resume_run.run_id, "previous_failed_run_id": resume_run.previous_failed_run_id,
            "contract_version": resume_run.pro_contract_version,
            "candidate_set_hash": resume_run.candidate_set_hash,
        }
        flash_audit_before = _flash_audit_count(session, context.flash_run.run_id)
        with temporary_real_llm_runtime():
            service = ProSingleV3Service(session, ledger)
            canary_reports = service.run_canary(
                resume_run, canary, context.selection_sources, checkpoint_path
            )
            report["pro_canary"] = {
                "status": "PASS", "count": 3, "reports": canary_reports,
                "expanded": True,
            }
            review_reports = service.run_reviews(
                resume_run, ordered, context.selection_sources, checkpoint_path,
                stage="FULL", stop_on_failure=True,
            )
            reviews = service.rank_and_apply(
                resume_run, ordered, context.selection_sources
            )
            if resume_run.portfolio_result:
                portfolio = ProPortfolioWireV3.model_validate(resume_run.portfolio_result)
                portfolio_report = _reused_v3_portfolio_report()
                resume_run.status = "PRO_SUCCESS"
                session.commit()
            else:
                portfolio, portfolio_report = service.run_portfolio(
                    resume_run, ordered, reviews, context.selection_sources
                )
            report["single_stock_reports"] = _merge_v3_review_reports(
                ordered, [*prior_candidate_reports, *canary_reports], review_reports
            )
            report["local_pro_ranking"] = {
                "status": "PASS", "count": len(reviews), "ranking_version": RANKING_VERSION,
                "rank_sequence": [row.pro_rank for row in reviews],
                "top_codes": [row.stock_code for row in reviews[:10]],
            }
            report["portfolio_summary"] = {
                "status": "PASS", "report": portfolio_report,
                "portfolio_risk_level": portfolio.portfolio_risk_level,
                "top_priority_stock_codes": portfolio.top_priority_stock_codes,
            }
        report["runtime_switch_restoration"] = _runtime_switch_status()
        if not report["runtime_switch_restoration"]["restored"]:
            raise RuntimeError("REAL_LLM_RUNTIME_SWITCH_RESTORE_FAILED")
        if _flash_audit_count(session, context.flash_run.run_id) != flash_audit_before:
            raise ValueError("FLASH_AUDIT_COUNT_CHANGED_DURING_V3_RESUME")
        ledger_after = ledger.summary(pro_resume_run_id=resume_run.run_id)
        if int(ledger_after["pro_candidate_actual"]) >= 300_000:
            raise PipelineBudgetExceeded("PRO_V3_CANDIDATE_BUDGET_EXCEEDED")
        if int(ledger_after["pro_candidate_repair_actual"]) >= 100_000:
            raise PipelineBudgetExceeded("PRO_V3_REPAIR_BUDGET_EXCEEDED")
        if int(ledger_after["pro_portfolio_actual"]) + int(ledger_after["pro_portfolio_repair_actual"]) >= 80_000:
            raise PipelineBudgetExceeded("PRO_V3_PORTFOLIO_BUDGET_EXCEEDED")
        if int(ledger_after["total_actual_api_tokens"]) >= 4_700_000:
            raise PipelineBudgetExceeded("TOTAL_TOKEN_SAFETY_RESERVE_REACHED")
        report["token_ledger"] = _v3_ledger_report(ledger_after, previous_v2)

        trader_service = TraderDemoService(session)
        plan_count = int(session.scalar(select(func.count()).select_from(ModelValidationOrderPlan).where(
            ModelValidationOrderPlan.validation_run_id == context.flash_run.run_id
        )) or 0)
        if plan_count == 0:
            trader_service.generate_candidate_outputs(
                context.flash_run.run_id,
                account_equity=Decimal(args.account_equity),
                available_cash=Decimal(args.available_cash),
            )
        payload = _serialize_readback(trader_service.readback(context.flash_run.run_id))
        _validate_resume_payload(payload)
        payload["budget"] = _excel_budget_payload(ledger_after)
        payload["pro_resume"] = {
            "run_id": resume_run.run_id, "contract_version": SINGLE_CONTRACT_VERSION,
            "candidate_set_hash": context.candidate_set_hash,
            "portfolio_result": resume_run.portfolio_result,
            "token_ledger": report["token_ledger"],
        }
        payload["content_hash"] = _payload_hash(payload)
        output_dir = (ROOT_DIR / args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        short_id = resume_run.run_id.replace("pro-resume-", "")[:8]
        final_path = output_dir / f"ai_trader_full_test_20260710_{short_id}.xlsx"
        if final_path.exists():
            raise FileExistsError(f"OUTPUT_EXISTS:{final_path.name}")
        temp_path = output_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp.xlsx"
        _build_excel(payload, temp_path, output_dir)
        validation = _validate_xlsx(temp_path, payload)
        os.replace(temp_path, final_path)
        workbook_hash = hashlib.sha256(final_path.read_bytes()).hexdigest()
        sidecar = _sidecar(payload, validation, final_path, workbook_hash)
        sidecar.update({"pro_resume": payload["pro_resume"], "token_ledger": report["token_ledger"]})
        sidecar_text = json.dumps(sidecar, ensure_ascii=False, indent=2, default=str)
        if not _secret_scan(sidecar_text):
            raise ValueError("SIDECAR_SECRET_SCAN_FAILED")
        audit_path = final_path.with_name(final_path.stem + "_audit.json")
        audit_path.write_text(sidecar_text, encoding="utf-8")
        resume_run.status = "COMPLETED"
        session.commit()
        single_review_metrics = _v3_single_review_metrics(session, resume_run.run_id, 26)
        report.update({
            "status": "SUCCESS",
            "single_stock_pro_reviews": single_review_metrics,
            "single_stock_failures": [],
            "order_plans": {"count": len(payload["order_rows"]), "source": "RULE_ENGINE", "actionable": False},
            "position_sizing": {"count": len(payload["order_rows"]), "source": "RULE_ENGINE", "actionable": False},
            "fundamental_completeness": {"status": "PASS", "count": len(payload["fundamental_rows"])},
            "excel_output": str(final_path), "excel_audit": str(audit_path),
            "excel_validation": validation, "workbook_sha256": workbook_hash,
            "workbook_row_counts": {
                "quant": len(payload["quant_rows"]), "flash": len(payload["llm_rows"]),
                "top20": payload["row_counts"]["llm_selected_count"],
                "manual": payload["row_counts"]["manual_selected_count"],
                "candidate": len(payload["order_rows"]), "order": len(payload["order_rows"]),
                "position": len(payload["order_rows"]), "fundamental": len(payload["fundamental_rows"]),
            },
            "quant_no_llm_regression": {
                "verified": context.quant_run.no_llm_call_verified,
                "per_stock_api_call_count": context.quant_run.per_stock_api_call_count,
                "flash_calls_repeated": 0,
            },
        })
        report["completed"].extend([
            "Three-stock Pro canary", "Twenty-six single-stock Pro reviews",
            "Deterministic ranking", "Portfolio summary", "Rule orders and positions", "Five-sheet Excel",
        ])
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_v3_report(report)
        print(json.dumps(_public_report(report), ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        if resume_run is not None and session is not None:
            resume_run.status = "PARTIAL_PRO_FAILURE"
            session.commit()
        report["status"] = "STOPPED"
        report["problems"].append(_redacted_error(exc))
        report["stopped_stages"] = ["PRO_CANDIDATE", "LOCAL_RANKING", "PORTFOLIO_SUMMARY", "ORDER_PLAN", "POSITION_SIZING", "EXCEL_EXPORT"]
        if isinstance(exc, ProSingleV3Failure):
            report["single_stock_failures"] = [exc.report]
            report["pro_failure_category"] = exc.category
        if ledger is not None and resume_run is not None:
            current = ledger.summary(pro_resume_run_id=resume_run.run_id)
            previous = ledger.summary(pro_resume_run_id=PREVIOUS_FAILED_RUN_ID)
            report["token_ledger"] = _v3_ledger_report(current, previous)
        report["runtime_switch_restoration"] = _runtime_switch_status()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_v3_report(report)
        print(json.dumps(_public_report(report), ensure_ascii=False, indent=2, default=str))
        return 2
    finally:
        if session is not None:
            session.close()


def _validate_resume_args(args: argparse.Namespace) -> None:
    if args.resume_stage != "PRO_AGGREGATION":
        raise ValueError("RESUME_STAGE_MUST_BE_PRO_AGGREGATION")
    if not args.reuse_flash_results:
        raise ValueError("RESUME_MUST_REUSE_FLASH_RESULTS")
    if args.new_pro_contract != PRO_CONTRACT_VERSION:
        raise ValueError("RESUME_PRO_CONTRACT_MISMATCH")
    if args.pro_chunk_size != 5:
        raise ValueError("RESUME_PRO_CHUNK_SIZE_MUST_BE_5")
    if args.real_llm and args.continue_on_stock_error:
        raise ValueError("RESUME_CONTINUE_ON_STOCK_ERROR_MUST_BE_FALSE")
    if Decimal(args.account_equity) != Decimal("1000000") or Decimal(args.available_cash) != Decimal("1000000"):
        raise ValueError("ISOLATED_ACCOUNT_ARGUMENT_MISMATCH")


def _validate_v3_args(args: argparse.Namespace) -> None:
    expected = {
        "resume_stage": "PRO_AGGREGATION",
        "new_pro_contract": SINGLE_CONTRACT_VERSION,
        "pro_candidate_mode": "single-stock",
        "pro_candidate_thinking": "disabled",
        "pro_candidate_max_tokens": 1800,
        "pro_portfolio_thinking": "disabled",
        "pro_portfolio_max_tokens": 3200,
    }
    for key, value in expected.items():
        if getattr(args, key) != value:
            raise ValueError(f"PRO_V3_ARGUMENT_MISMATCH:{key}")
    if not args.reuse_flash_results:
        raise ValueError("RESUME_MUST_REUSE_FLASH_RESULTS")
    if args.real_llm and args.continue_on_stock_error:
        raise ValueError("RESUME_CONTINUE_ON_STOCK_ERROR_MUST_BE_FALSE")
    if Decimal(args.account_equity) != Decimal("1000000") or Decimal(args.available_cash) != Decimal("1000000"):
        raise ValueError("ISOLATED_ACCOUNT_ARGUMENT_MISMATCH")
    config = ConfigManager().get_llm_gateway_config().get("pro_resume_v3", {})
    config_checks = {
        "candidate_mode": args.pro_candidate_mode,
        "candidate_thinking": args.pro_candidate_thinking,
        "candidate_max_tokens": args.pro_candidate_max_tokens,
        "portfolio_thinking": args.pro_portfolio_thinking,
        "portfolio_max_tokens": args.pro_portfolio_max_tokens,
        "concurrency": 3,
    }
    for key, value in config_checks.items():
        if config.get(key) != value:
            raise ValueError(f"PRO_V3_CONFIG_SERVICE_MISMATCH:{key}")


def _create_v3_resume_run(session, context) -> ProResumeRun:
    running = session.scalar(select(ProResumeRun).where(
        ProResumeRun.flash_validation_run_id == context.flash_run.run_id,
        ProResumeRun.candidate_set_hash == context.candidate_set_hash,
        ProResumeRun.pro_contract_version == SINGLE_CONTRACT_VERSION,
        ProResumeRun.status == "RUNNING",
    ).order_by(ProResumeRun.id.desc()))
    if running is not None:
        return running
    latest_failed = session.scalar(select(ProResumeRun).where(
        ProResumeRun.flash_validation_run_id == context.flash_run.run_id,
        ProResumeRun.status == "PARTIAL_PRO_FAILURE",
    ).order_by(ProResumeRun.id.desc()))
    if latest_failed is not None and latest_failed.pro_contract_version == SINGLE_CONTRACT_VERSION:
        successful = int(session.scalar(select(func.count()).select_from(ProCandidateReview).where(
            ProCandidateReview.pro_resume_run_id == latest_failed.run_id,
            ProCandidateReview.review_status == "SUCCESS",
            ProCandidateReview.contract_version == SINGLE_CONTRACT_VERSION,
        )) or 0)
        if successful == 26 and latest_failed.portfolio_result:
            latest_failed.status = "RUNNING"
            session.commit()
            return latest_failed
    previous_id = latest_failed.run_id if latest_failed is not None else PREVIOUS_FAILED_RUN_ID
    row = ProResumeRun(
        run_id=f"pro-resume-{uuid.uuid4().hex[:20]}", pipeline_run_id=PIPELINE_RUN_ID,
        quant_run_id=context.quant_run.run_id, manifest_id=context.manifest.manifest_id,
        flash_validation_run_id=context.flash_run.run_id,
        previous_failed_run_id=previous_id,
        pro_contract_version=SINGLE_CONTRACT_VERSION,
        prompt_version=SINGLE_PROMPT_VERSION,
        portfolio_prompt_version=PORTFOLIO_PROMPT_VERSION_V3,
        base_trade_date=context.quant_run.base_market_trade_date,
        target_trade_date=context.quant_run.target_trade_date,
        top20_hash=context.top20_hash, manual_hash=context.manual_hash,
        candidate_set_hash=context.candidate_set_hash,
        candidate_count=26, chunk_size=1, chunk_count=26, status="RUNNING",
        config_snapshot={
            "candidate_mode": "SINGLE_STOCK", "concurrency": 3,
            "candidate_thinking": "disabled", "candidate_max_tokens": 1800,
            "portfolio_thinking": "disabled", "portfolio_max_tokens": 3200,
            "candidate_codes": [normalize_ts_code(sample.stock_code) for sample in stable_v3_order(context.candidates)],
            "selection_sources": context.selection_sources,
            "quant_rerun": False, "flash_rerun": False,
        },
        portfolio_result={}, warnings=["MODEL_VALIDATION", "NON_ACTIONABLE"],
    )
    session.add(row)
    session.flush()
    if latest_failed is not None and latest_failed.pro_contract_version == SINGLE_CONTRACT_VERSION:
        _copy_v3_success_reviews(session, latest_failed.run_id, row.run_id)
    session.commit()
    return row


def _copy_v3_success_reviews(session, source_run_id: str, target_run_id: str) -> int:
    rows = list(session.scalars(select(ProCandidateReview).where(
        ProCandidateReview.pro_resume_run_id == source_run_id,
        ProCandidateReview.review_status == "SUCCESS",
        ProCandidateReview.contract_version == SINGLE_CONTRACT_VERSION,
        ProCandidateReview.prompt_version == SINGLE_PROMPT_VERSION,
    )))
    for source in rows:
        session.add(ProCandidateReview(
            pro_resume_run_id=target_run_id,
            flash_validation_run_id=source.flash_validation_run_id,
            chunk_id="single-resume-reused",
            contract_version=source.contract_version,
            candidate_input_hash=source.candidate_input_hash,
            stock_code=source.stock_code,
            pro_score=source.pro_score,
            pro_rank=None,
            ranking_tie_break_reason=None,
            ranking_version=None,
            priority=source.priority,
            final_summary=source.final_summary,
            key_strengths=list(source.key_strengths or []),
            key_risks=list(source.key_risks or []),
            fundamental_quality=source.fundamental_quality,
            quant_llm_consistency=source.quant_llm_consistency,
            manual_review_priority=source.manual_review_priority,
            data_conflict=source.data_conflict,
            prompt_version=source.prompt_version,
            actual_model=source.actual_model,
            review_status="SUCCESS",
        ))
    return len(rows)


def _merge_v3_review_reports(candidates, canary_reports, full_reports) -> list[dict[str, Any]]:
    by_code = {report["stock_code"]: report for report in full_reports}
    for report in canary_reports:
        if report.get("usage_source") != "RESUME_REUSED":
            by_code[report["stock_code"]] = report
    return [by_code[normalize_ts_code(sample.stock_code)] for sample in candidates]


def _reused_v3_portfolio_report() -> dict[str, Any]:
    return {
        "stock_code": "portfolio",
        "actual_model": "deepseek-v4-pro",
        "resolved_thinking_mode": "disabled",
        "schema_status": "PASS",
        "repair_attempted": False,
        "input_tokens": 0,
        "output_tokens": 0,
        "latency_ms": 0,
        "cost_usd": 0,
        "finish_reason": "stop",
        "diagnostics": {"schema_status": "PASS", "usage_source": "RESUME_REUSED"},
        "usage_persisted": True,
        "usage_source": "RESUME_REUSED",
    }


def _v3_single_review_metrics(session, run_id: str, expected_count: int) -> dict[str, Any]:
    rows = list(session.scalars(select(LLMUsage).where(
        LLMUsage.pro_resume_run_id == run_id,
        LLMUsage.task.in_([
            "pro_candidate_single_review",
            "pro_candidate_single_review_repair",
        ]),
    )))
    initial = [row for row in rows if row.task == "pro_candidate_single_review"]
    repair = [row for row in rows if row.task == "pro_candidate_single_review_repair"]
    called_codes = {
        str(row.call_id or "").split(":", 2)[1]
        for row in initial
        if str(row.call_id or "").count(":") >= 2
    }
    success_count = int(session.scalar(select(func.count()).select_from(ProCandidateReview).where(
        ProCandidateReview.pro_resume_run_id == run_id,
        ProCandidateReview.review_status == "SUCCESS",
        ProCandidateReview.contract_version == SINGLE_CONTRACT_VERSION,
    )) or 0)
    return {
        "total_candidates": expected_count,
        "success": success_count,
        "failure": expected_count - success_count,
        "reused": max(0, expected_count - len(called_codes)),
        "new_calls": len(initial),
        "repair_calls": len(repair),
        "input_tokens": sum(int(row.input_tokens or 0) for row in rows),
        "output_tokens": sum(int(row.output_tokens or 0) for row in rows),
        "cost_usd": round(sum(float(row.cost_usd or 0) for row in rows), 8),
        "duration_seconds": round(sum(int(row.latency_ms or 0) for row in rows) / 1000, 3),
    }


def _v3_estimate(candidates, sources) -> dict[str, Any]:
    input_tokens = 0
    for sample in candidates:
        payload, _ = _single_input(sample, sources)
        input_tokens += len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)) // 4
    expected_candidate_output = 26 * 420
    reserved_candidate_output = 26 * 1800
    portfolio_input = 12_000
    expected_portfolio_output = 1_200
    reserved_portfolio_output = 3_200
    return {
        "candidate_estimated_input_tokens": input_tokens,
        "candidate_expected_output_tokens": expected_candidate_output,
        "candidate_reserved_output_tokens": reserved_candidate_output,
        "candidate_call_count": 26,
        "candidate_repair_reserve": 100_000,
        "portfolio_estimated_input_tokens": portfolio_input,
        "portfolio_expected_output_tokens": expected_portfolio_output,
        "portfolio_reserved_output_tokens": reserved_portfolio_output,
        "portfolio_call_count": 1,
        "final_reserve": 100_000,
        "reserved_total_tokens": input_tokens + reserved_candidate_output + 100_000 + portfolio_input + reserved_portfolio_output + 100_000,
    }


def _v3_ledger_report(current: dict[str, Any], previous_v2: dict[str, Any]) -> dict[str, Any]:
    total = int(current["total_actual_api_tokens"])
    return {
        "connectivity_actual": int(current["connectivity_actual"]),
        "flash_actual": int(current["flash_actual"]),
        "flash_repair_actual": int(current["flash_repair_actual"]),
        "pro_v1_unavailable_count": int(current["unavailable_usage_count"]),
        "previous_pro_v2_actual": int(previous_v2["current_resume_new_tokens"]),
        "current_pro_candidate_actual": int(current["pro_candidate_actual"]),
        "current_pro_candidate_repair": int(current["pro_candidate_repair_actual"]),
        "current_portfolio_actual": int(current["pro_portfolio_actual"]),
        "current_portfolio_repair": int(current["pro_portfolio_repair_actual"]),
        "pipeline_total_actual_api_tokens": total,
        "current_resume_new_api_tokens": int(current["current_resume_new_tokens"]),
        "historical_reused_api_tokens": int(current["historical_reused_tokens"]),
        "remaining_budget_from_known_actual": 5_000_000 - total,
        "total_cost_usd": current["total_cost_usd"],
    }


def _write_v3_report(report: dict[str, Any]) -> None:
    path = ROOT_DIR / "data" / "reports" / "pro_single_v3_pipeline_20260710_report.json"
    path.write_text(json.dumps(_public_report(report), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _get_or_create_pro_resume_run(session, context, chunk_size: int) -> ProResumeRun:
    existing = session.scalar(
        select(ProResumeRun)
        .where(
            ProResumeRun.flash_validation_run_id == context.flash_run.run_id,
            ProResumeRun.candidate_set_hash == context.candidate_set_hash,
            ProResumeRun.pro_contract_version == PRO_CONTRACT_VERSION,
            ProResumeRun.status.in_(["RUNNING", "PARTIAL_PRO_FAILURE", "PRO_SUCCESS"]),
        )
        .order_by(ProResumeRun.id.desc())
    )
    if existing is not None:
        return existing
    chunks = chunk_candidates(context.candidates, chunk_size)
    row = ProResumeRun(
        run_id=f"pro-resume-{uuid.uuid4().hex[:20]}", pipeline_run_id=PIPELINE_RUN_ID,
        quant_run_id=context.quant_run.run_id, manifest_id=context.manifest.manifest_id,
        flash_validation_run_id=context.flash_run.run_id,
        pro_contract_version=PRO_CONTRACT_VERSION,
        prompt_version=PRO_CANDIDATE_PROMPT_VERSION,
        portfolio_prompt_version=PRO_PORTFOLIO_PROMPT_VERSION,
        base_trade_date=context.quant_run.base_market_trade_date,
        target_trade_date=context.quant_run.target_trade_date,
        top20_hash=context.top20_hash, manual_hash=context.manual_hash,
        candidate_set_hash=context.candidate_set_hash, candidate_count=26,
        chunk_size=chunk_size, chunk_count=len(chunks), status="RUNNING",
        config_snapshot={
            "candidate_codes": context.candidate_codes,
            "selection_sources": context.selection_sources,
            "reasoning_effort_candidate": "high", "reasoning_effort_portfolio": "max",
            "quant_rerun": False, "flash_rerun": False,
        },
        portfolio_result={}, warnings=["MODEL_VALIDATION", "NON_ACTIONABLE"],
    )
    session.add(row)
    session.commit()
    return row


def _resume_pro_estimate(context, chunks) -> dict[str, Any]:
    candidate_input = 0
    for index, chunk in enumerate(chunks, start=1):
        compact = [
            ProResumeService._compact_candidate(sample, context.selection_sources)
            for sample in chunk
        ]
        request = ProResumeService._candidate_request(f"candidate-{index:02d}", compact, args_model_alias())
        candidate_input += sum(len(message.content) for message in request.messages) // 4
    portfolio_input = 18_000
    expected_output = len(chunks) * 900 + 900
    reserved_output = len(chunks) * 1_800 + 1_400
    return {
        "expected_pro_input_tokens": candidate_input + portfolio_input,
        "expected_pro_output_tokens": expected_output,
        "reserved_pro_input_tokens": candidate_input + portfolio_input,
        "reserved_pro_output_tokens": reserved_output,
        "reserved_pro_tokens": candidate_input + portfolio_input + reserved_output,
        "pro_sub_budget": 800_000,
    }


def args_model_alias() -> str:
    return "controller-high-capability"


def _historical_canary_projection(session) -> dict[str, Any]:
    rows = list(session.scalars(
        select(ModelValidationLLMAudit).where(
            ModelValidationLLMAudit.validation_run_id == "trader-demo-751b7d770cf343cfb29f",
            ModelValidationLLMAudit.cache_status != "REUSED",
        )
    ))
    if not rows:
        return {
            "usage_source": "SERIALIZED_ESTIMATE", "input_p50": 4035, "input_p90": 4326,
            "output_p50": 210, "output_p90": 264, "projected_flash_total": 963900,
        }
    projection = canary_projection(
        [row.input_tokens for row in rows], [row.output_tokens for row in rows],
        total_task_count=210, repair_probability=0.05,
    )
    return {"usage_source": "REUSED_HISTORICAL_USAGE", **projection, "projected_flash_total": projection["projected_total"]}


def _legacy_pro_diagnosis(session, validation_run_id: str) -> dict[str, Any]:
    row = session.scalar(
        select(ModelValidationLLMAudit).where(
            ModelValidationLLMAudit.validation_run_id == validation_run_id,
            ModelValidationLLMAudit.task == "daily_pro_aggregation",
        ).order_by(ModelValidationLLMAudit.id.desc())
    )
    diagnostics = dict(row.diagnostics or {}) if row else {}
    return {
        "status": "LEGACY_PRO_FAILURE_DIAGNOSTICS_INCOMPLETE",
        "provider_http_status": diagnostics.get("http_status"),
        "actual_model": row.actual_model if row else "deepseek-v4-pro",
        "thinking_mode": diagnostics.get("thinking_mode"),
        "reasoning_effort": diagnostics.get("reasoning_effort"),
        "finish_reason": diagnostics.get("finish_reason"),
        "content_empty": diagnostics.get("content_empty"),
        "content_length": diagnostics.get("content_length"),
        "schema_error_paths": diagnostics.get("schema_error_paths") or [],
        "repair_attempted": bool(diagnostics.get("repair_attempted", True)),
        "usage_persisted": False,
        "error_category": row.error_category if row else "PRO_AGGREGATION_SCHEMA_FAILED",
        "raw_response_stored": False, "reasoning_stored": False,
    }


def _repair_legacy_report(ledger: dict[str, Any], projection: dict[str, Any]) -> None:
    try:
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        report = {}
    report["stopped_stages"] = ["PRO_AGGREGATION", "ORDER_PLAN", "POSITION_SIZING", "EXCEL_EXPORT"]
    report["pro_aggregation"] = {
        "status": "FAILED", "run_id": "legacy-pro-v1", "contract_version": "daily-pro-aggregation-v1",
        "prompt_version": "daily-pro-aggregation-v1", "input_stock_count": 26,
        "chunk_count": 0, "successful_chunk_count": 0, "failed_chunk_count": 1,
        "provider_status": "SUCCESS", "http_status": 0, "finish_reason": None,
        "content_empty": False, "content_length": 0, "json_parse_status": "UNAVAILABLE",
        "schema_status": "PRO_AGGREGATION_SCHEMA_FAILED", "schema_error_paths": [],
        "repair_attempted": True, "usage_persisted": False,
        "error_category": "LEGACY_PRO_FAILURE_DIAGNOSTICS_INCOMPLETE",
    }
    report["post_canary_projection"] = projection
    report["authoritative_token_ledger"] = ledger
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _flash_audit_count(session, validation_run_id: str) -> int:
    return int(session.scalar(
        select(func.count()).select_from(ModelValidationLLMAudit).where(
            ModelValidationLLMAudit.validation_run_id == validation_run_id,
            ModelValidationLLMAudit.task.in_(["fundamental_structured_inference", "structured_light_screening"]),
        )
    ) or 0)


def _validate_resume_payload(payload: dict[str, Any]) -> None:
    counts = payload["row_counts"]
    expected = {
        "quant_sheet_rows": 5308, "llm_sheet_rows": 105,
        "llm_selected_count": 20, "manual_selected_count": 7,
        "trading_candidate_count": 26, "order_sheet_rows": 26,
        "fundamental_sheet_rows": 26,
    }
    for key, value in expected.items():
        if counts.get(key) != value:
            raise ValueError(f"RESUME_WORKBOOK_ROW_COUNT_MISMATCH:{key}:{counts.get(key)}")
    required = (
        "industry_chain", "chain_position", "level_one_sector", "classification_standard",
        "main_business", "core_products", "industry_position", "concept_tags",
        "structural_theme_fit", "competitive_advantage", "industry_trend",
        "investment_logic", "logic_invalidation", "domestic_substitution",
        "observation_rating", "financial_status", "financial_status_reason",
        "pro_summary", "pro_strengths", "pro_risks",
        "pro_fundamental_quality", "pro_quant_consistency", "pro_manual_review_priority",
    )
    for row in payload["fundamental_rows"]:
        for key in required:
            if row.get(key) in (None, "", [], {}):
                raise ValueError(f"RESUME_FUNDAMENTAL_FIELD_EMPTY:{row['stock_code']}:{key}")


def _excel_budget_payload(ledger: dict[str, Any]) -> dict[str, Any]:
    total = int(ledger["total_actual_api_tokens"])
    return {
        "limits": {
            "daily_limit": 5_000_000, "warning_threshold": 4_000_000,
            "final_reserve": 300_000,
        },
        "usage": {
            "flash": int(ledger["flash_actual"]) + int(ledger["flash_repair_actual"]),
            "pro": int(ledger["pro_candidate_actual"]) + int(ledger["pro_portfolio_actual"]),
            "repair": int(ledger["pro_candidate_repair_actual"]) + int(ledger["pro_portfolio_repair_actual"]),
            "connectivity": int(ledger["connectivity_actual"]),
        },
        "total": total, "remaining": 5_000_000 - total,
        "warning_triggered": total >= 4_000_000,
    }


def _write_resume_report(report: dict[str, Any]) -> None:
    path = ROOT_DIR / "data" / "reports" / "pro_resume_pipeline_20260710_report.json"
    public = _public_report(report)
    path.write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _env_true(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _bool_arg(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


if __name__ == "__main__":
    raise SystemExit(main())
