from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from database.models.validation import ModelValidationRun, ModelValidationSample
from database.session import get_session, init_db
from research.flash_v4 import FLASH_SCORE_VERSION, assert_flash_batch_quality
from trader_demo.runtime import temporary_real_llm_runtime
from trader_demo.service import ManualSelection, TraderDemoService


QUANT_RUN_ID = "quant-840384b9e637e1143f243083"
CANARY_RANKS = (1, 10, 50, 100, 545)
MANUAL = [
    ManualSelection("300145.SZ", "人工池：南方泵业", "HIGH"),
    ManualSelection("300821.SZ", "人工池：东岳硅材", "HIGH"),
    ManualSelection("301151.SZ", "人工池：冠龙节能", "HIGH"),
    ManualSelection("301356.SZ", "人工池：天振股份", "HIGH"),
    ManualSelection("603726.SH", "人工池：朗迪集团", "HIGH"),
    ManualSelection("603019.SH", "人工池：中科曙光", "HIGH"),
    ManualSelection("002409.SZ", "人工池：雅克科技", "HIGH"),
]
CHECKPOINT = ROOT / "outputs" / "2026-07-10" / "审计" / "flash_v4_checkpoint.json"
REPORT = ROOT / "data" / "reports" / "flash_v4_pipeline_20260710_report.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Flash V4 model-validation pipeline")
    parser.add_argument("--stage", choices=["canary", "retry", "full"], required=True)
    parser.add_argument("--real-llm", action="store_true")
    args = parser.parse_args()
    if not args.real_llm:
        raise ValueError("FLASH_V4_REAL_LLM_CONFIRMATION_REQUIRED")
    init_db()
    session = get_session()
    try:
        service = TraderDemoService(session)
        with temporary_real_llm_runtime():
            if args.stage == "canary":
                run_id = service.run(
                    quant_run_id=QUANT_RUN_ID,
                    ranks=CANARY_RANKS,
                    top_n=None,
                    manual=[],
                    selected_decisions={"ADVANCE", "HOLD", "WATCH_ONLY"},
                    account_equity=Decimal("1000000"),
                    available_cash=Decimal("1000000"),
                    continue_on_stock_error=True,
                    analysis_only=True,
                    defer_candidate_generation=True,
                    concurrency=5,
                    batch_size=10,
                    checkpoint_callback=lambda value: _checkpoint("FLASH_V4_CANARY", value),
                )
                result = _validate_run(session, run_id, canary=True)
                _checkpoint("FLASH_V4_CANARY_PASS", {"run_id": run_id, **result})
            elif args.stage == "retry":
                retry_codes = {
                    "000518.SZ", "300821.SZ", "600879.SH", "603268.SH",
                    "603726.SH", "688111.SH", "688222.SH",
                }
                run_id = service.run(
                    quant_run_id=QUANT_RUN_ID,
                    ranks=None,
                    top_n=100,
                    manual=MANUAL,
                    selected_decisions={"ADVANCE", "HOLD", "WATCH_ONLY"},
                    account_equity=Decimal("1000000"), available_cash=Decimal("1000000"),
                    continue_on_stock_error=True, retry_failed_only=True,
                    retry_stocks=retry_codes, reuse_successful=True,
                    analysis_only=True, defer_candidate_generation=True,
                    concurrency=5, batch_size=10,
                    checkpoint_callback=lambda value: _checkpoint("FLASH_V4_RETRY", value),
                )
                result = _validate_run(session, run_id, canary=False)
                if result["run_status"] != "SUCCESS":
                    raise ValueError("FLASH_V4_RETRY_NOT_FULLY_SUCCESSFUL")
                _checkpoint("FLASH_V4_RETRY_PASS", {"run_id": run_id, **result})
            else:
                _require_canary_pass()
                run_id = service.run(
                    quant_run_id=QUANT_RUN_ID,
                    ranks=None,
                    top_n=100,
                    manual=MANUAL,
                    selected_decisions={"ADVANCE", "HOLD", "WATCH_ONLY"},
                    account_equity=Decimal("1000000"),
                    available_cash=Decimal("1000000"),
                    continue_on_stock_error=True,
                    reuse_successful=True,
                    model_validation_top_n=20,
                    analysis_only=True,
                    defer_candidate_generation=True,
                    concurrency=5,
                    batch_size=10,
                    checkpoint_callback=lambda value: _checkpoint("FLASH_V4_FULL", value),
                )
                result = _validate_run(session, run_id, canary=False)
                _checkpoint("FLASH_V4_FULL_PASS", {"run_id": run_id, **result})
        report = {
            "phase": "Flash V4 Meaningful Scoring",
            "status": "PASS",
            "stage": args.stage,
            "run_id": run_id,
            "quant_run_id": QUANT_RUN_ID,
            "quant_rerun": False,
            "flash_score_version": FLASH_SCORE_VERSION,
            **result,
            "runtime_switch_restoration": _runtime_switches(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


def _validate_run(session, run_id: str, *, canary: bool) -> dict:
    run = session.scalar(select(ModelValidationRun).where(ModelValidationRun.run_id == run_id))
    samples = list(session.scalars(select(ModelValidationSample).where(
        ModelValidationSample.validation_run_id == run_id
    )))
    successful = [
        sample for sample in samples
        if (sample.screening_result or {}).get("llm_score") is not None
        and (sample.screening_result or {}).get("screening_decision") not in {
            "SCHEMA_ERROR", "PROVIDER_ERROR", "INVALID_JSON", "JSON_TRUNCATED"
        }
    ]
    if canary and (run is None or len(successful) != 5):
        raise ValueError(f"FLASH_V4_CANARY_SUCCESS_COUNT_INVALID:{len(successful)}/5")
    quality = assert_flash_batch_quality(
        [sample.screening_result or {} for sample in successful], canary=canary
    )
    chain_known = sum(
        str(((sample.fundamental_result or {}).get("industry_chain") or {}).get("chain_name") or "UNKNOWN").upper()
        not in {"UNKNOWN", "信息不足*"}
        for sample in successful
    )
    products_known = sum(
        bool((sample.fundamental_result or {}).get("core_products"))
        and (sample.fundamental_result or {}).get("core_products") != ["信息不足*"]
        for sample in successful
    )
    if canary and (chain_known < 3 or products_known < 3):
        raise ValueError("FLASH_V4_CANARY_FUNDAMENTAL_SEMANTICS_FAILED")
    scores = sorted(float((sample.screening_result or {})["llm_score"]) for sample in successful)
    decisions = Counter(str((sample.screening_result or {}).get("screening_decision")) for sample in successful)
    return {
        "run_status": run.status if run else "MISSING",
        "sample_count": len(samples),
        "success_count": len(successful),
        "failure_count": len(samples) - len(successful),
        "quality_gate": quality,
        "chain_known_count": chain_known,
        "core_products_known_count": products_known,
        "score_min": min(scores),
        "score_max": max(scores),
        "decision_distribution": dict(decisions),
    }


def _checkpoint(stage: str, value: dict) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        previous = {}
    payload = {
        "stage": stage,
        "quant_run_id": QUANT_RUN_ID,
        "quant_rerun": False,
        "flash_score_version": FLASH_SCORE_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "canary_passed": bool(previous.get("canary_passed")) or stage == "FLASH_V4_CANARY_PASS",
        **value,
    }
    CHECKPOINT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _require_canary_pass() -> None:
    try:
        payload = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("FLASH_V4_CANARY_CHECKPOINT_REQUIRED") from exc
    if not payload.get("canary_passed") and payload.get("stage") not in {
        "FLASH_V4_CANARY_PASS", "FLASH_V4_FULL_PASS", "FLASH_V4_RETRY_PASS"
    }:
        raise ValueError("FLASH_V4_CANARY_NOT_PASSED")


def _runtime_switches() -> dict[str, str]:
    import os

    return {
        "LLM_REAL_CALLS_ENABLED": os.getenv("LLM_REAL_CALLS_ENABLED", "false"),
        "RUN_REAL_FUNDAMENTAL_RESEARCH": os.getenv("RUN_REAL_FUNDAMENTAL_RESEARCH", "false"),
        "LLM_GATEWAY_MOCK_ONLY": os.getenv("LLM_GATEWAY_MOCK_ONLY", "true"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
