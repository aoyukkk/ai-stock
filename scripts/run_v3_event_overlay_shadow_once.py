from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db
from event_overlay.checkpoint import CHECKPOINT_CONTRACT_VERSION
from event_overlay.constants import (
    EVENT_REVIEW_VERSION,
    SCREENING_VERSION,
    SEARCH_CONTRACT_VERSION,
)
from event_overlay.hashing import file_hash
from event_overlay.service import EventOverlayShadowService, RunOptions, default_decision_as_of
from reporting.web_result_publish import publish_internal_web_snapshot


SHANGHAI = ZoneInfo("Asia/Shanghai")
DAILY_READY = "V3_1_DAILY_SHADOW_READY"
DAILY_REUSED = "REUSED_EXISTING_V3_1_DAILY_SHADOW"


def load_runtime_environment() -> None:
    load_dotenv(ROOT / ".env", override=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent V3 Event Overlay Shadow runner.")
    parser.add_argument("--trade-date", required=True, type=date.fromisoformat)
    parser.add_argument("--base-quant-run-id")
    parser.add_argument("--run-id")
    parser.add_argument("--decision-as-of-time", type=datetime.fromisoformat)
    parser.add_argument("--enable-real-search", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--mock-search", action="store_true")
    parser.add_argument("--stock-limit", type=int)
    parser.add_argument("--skip-pro", action="store_true", default=True)
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument(
        "--daily-real-search",
        action="store_true",
        help=(
            "Run the formal idempotent V3.1 daily sequence: reuse a matching "
            "ready Top100 or run a 5-stock canary followed by the full Top100."
        ),
    )
    parser.add_argument(
        "--confirm-full-search",
        action="store_true",
        help="Allow a guarded Top100 real-search run after a matching successful 5-stock canary.",
    )
    args = parser.parse_args()
    # Keep the V3 entrypoint consistent with the official V2 runner.  Real
    # direct-search remains guarded by LLM_REAL_CALLS_ENABLED and the canary
    # size contract; this only makes the project's existing local settings
    # visible to the process.
    load_runtime_environment()
    if args.daily_real_search:
        if any((
            args.enable_real_search,
            args.mock_search,
            args.no_network,
            args.export_only,
            args.stock_limit is not None,
            args.confirm_full_search,
        )):
            parser.error("--daily-real-search cannot be combined with single-run modes")
        report = run_daily_v31(
            args.trade_date,
            base_quant_run_id=args.base_quant_run_id,
            decision_as_of_time=args.decision_as_of_time,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        web_sync_failed = (report.get("web_sync") or {}).get("status") == "FAILED"
        return 0 if (
            report.get("status") in {DAILY_READY, DAILY_REUSED}
            and not web_sync_failed
        ) else 2
    if args.export_only:
        if not args.run_id:
            raise SystemExit("EXPORT_ONLY_REQUIRES_RUN_ID; no business calls were made")
        output_dir = ROOT / "outputs" / "event_overlay" / args.trade_date.isoformat() / args.run_id
        manifest_path = output_dir / "run_manifest.json"
        if not manifest_path.is_file():
            raise SystemExit("EXPORT_ONLY_RUN_NOT_FOUND; no business calls were made")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(json.dumps({
            "status": "EXPORT_ONLY_REUSED_EXISTING_ARTIFACTS",
            "run_id": args.run_id,
            "output_dir": str(output_dir),
            "screening_version": manifest.get("screening_version"),
            "actual_network_calls": 0,
            "logical_evaluations": 0,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }, ensure_ascii=False, indent=2))
        return 0
    init_db()
    session = get_session()
    try:
        report = EventOverlayShadowService(session).run(RunOptions(
            trade_date=args.trade_date,
            decision_as_of_time=args.decision_as_of_time or default_decision_as_of(args.trade_date),
            base_quant_run_id=args.base_quant_run_id,
            enable_real_search=args.enable_real_search,
            mock_search=args.mock_search,
            no_network=args.no_network,
            stock_limit=args.stock_limit,
            skip_pro=args.skip_pro,
            resume=args.resume,
            confirm_full_search=args.confirm_full_search,
        ))
        report["web_sync"] = _publish_full_v31_if_eligible(report)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    web_sync_failed = (report.get("web_sync") or {}).get("status") == "FAILED"
    return 0 if (
        report.get("status") == "V3_EVENT_OVERLAY_SHADOW_READY"
        and not web_sync_failed
    ) else 2


def run_daily_v31(
    trade_date: date,
    *,
    base_quant_run_id: str | None = None,
    decision_as_of_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the formal, resumable V3.1 Shadow daily sequence."""

    load_runtime_environment()
    _assert_daily_safety_guards(require_provider=False)
    init_db()
    expected = _expected_daily_contract(trade_date, base_quant_run_id)
    ready = _find_matching_manifest(trade_date, expected, input_count=100)
    if ready is not None:
        web_sync = publish_internal_web_snapshot()
        return {
            "status": DAILY_REUSED,
            "trade_date": trade_date.isoformat(),
            "run_id": ready["run_id"],
            "decision_as_of_time": ready["decision_as_of_time"],
            "source_run_id": ready["source_run_id"],
            "input_count": 100,
            "successful_evaluation_count": 100,
            "actual_network_calls": 0,
            "checkpoint_reused": 100,
            "output_dir": ready["_output_dir"],
            "web_sync": web_sync,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }

    _assert_daily_safety_guards(require_provider=True)
    canary = _find_matching_manifest(trade_date, expected, input_count=5)
    if decision_as_of_time is None and canary is not None:
        decision_as_of_time = datetime.fromisoformat(canary["decision_as_of_time"])
    decision_as_of_time = decision_as_of_time or datetime.now(SHANGHAI).replace(
        microsecond=0
    )

    canary_report: dict[str, Any]
    if (
        canary is None
        or canary["decision_as_of_time"] != decision_as_of_time.isoformat()
    ):
        canary_report = _run_event_overlay_once(RunOptions(
            trade_date=trade_date,
            decision_as_of_time=decision_as_of_time,
            base_quant_run_id=base_quant_run_id,
            enable_real_search=True,
            stock_limit=5,
            skip_pro=True,
        ))
        if (
            canary_report.get("status") != "V3_EVENT_OVERLAY_SHADOW_READY"
            or int(canary_report.get("input_count") or 0) != 5
            or int(canary_report.get("search_failure_count") or 0) != 0
            or int(canary_report.get("provider_failure_count") or 0) != 0
        ):
            raise RuntimeError("V3_1_DAILY_CANARY_FAILED")
    else:
        canary_report = {
            "status": "REUSED_SUCCESSFUL_CANARY",
            "run_id": canary["run_id"],
            "actual_network_calls": 0,
            "checkpoint_reused": 5,
        }

    full_report = _run_event_overlay_once(RunOptions(
        trade_date=trade_date,
        decision_as_of_time=decision_as_of_time,
        base_quant_run_id=base_quant_run_id,
        enable_real_search=True,
        stock_limit=100,
        skip_pro=True,
        confirm_full_search=True,
    ))
    if (
        full_report.get("status") != "V3_EVENT_OVERLAY_SHADOW_READY"
        or int(full_report.get("input_count") or 0) != 100
        or int(full_report.get("search_failure_count") or 0) != 0
        or int(full_report.get("provider_failure_count") or 0) != 0
    ):
        raise RuntimeError(
            "V3_1_DAILY_FULL_FAILED:"
            f"{full_report.get('run_id')}:"
            f"{full_report.get('search_failure_count')}:"
            f"{full_report.get('provider_failure_count')}"
        )
    web_sync = _publish_full_v31_if_eligible(full_report)
    if web_sync.get("status") == "FAILED":
        raise RuntimeError("V3_1_DAILY_WEB_SYNC_FAILED")
    return {
        "status": DAILY_READY,
        "trade_date": trade_date.isoformat(),
        "decision_as_of_time": decision_as_of_time.isoformat(),
        "source_run_id": full_report.get("source_run_id"),
        "run_id": full_report.get("run_id"),
        "canary": canary_report,
        "full": full_report,
        "web_sync": web_sync,
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
    }


def _run_event_overlay_once(options: RunOptions) -> dict[str, Any]:
    session = get_session()
    try:
        return EventOverlayShadowService(session).run(options)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _assert_daily_safety_guards(*, require_provider: bool) -> None:
    if os.getenv("ENABLE_REAL_TRADING", "false").strip().lower() != "false":
        raise RuntimeError("ENABLE_REAL_TRADING_MUST_REMAIN_FALSE")
    if not require_provider:
        return
    if os.getenv("LLM_REAL_CALLS_ENABLED", "").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        raise RuntimeError("V3_1_DAILY_REAL_SEARCH_NOT_ENABLED")
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        raise RuntimeError("V3_1_DAILY_DEEPSEEK_KEY_MISSING")


def _expected_daily_contract(
    trade_date: date,
    base_quant_run_id: str | None,
) -> dict[str, str]:
    source_path = (
        ROOT
        / "outputs"
        / "quant_v2_validation"
        / trade_date.isoformat()
        / "quant_v2_validation.json"
    )
    universe_path = source_path.parent / "v2_full_universe.csv"
    prompt_path = ROOT / "prompts" / "event_overlay_v3_1_flash.yaml"
    if not source_path.is_file():
        raise FileNotFoundError(f"V2_QUANT_SOURCE_NOT_FOUND:{source_path}")
    if not universe_path.is_file():
        raise FileNotFoundError(f"V2_FULL_UNIVERSE_NOT_FOUND:{universe_path}")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source_run_id = str(source.get("run_id") or "")
    if base_quant_run_id and source_run_id != base_quant_run_id:
        raise ValueError("BASE_QUANT_RUN_ID_MISMATCH")
    return {
        "source_run_id": source_run_id,
        "source_hash": file_hash(source_path),
        "universe_hash": file_hash(universe_path),
        "prompt_hash": file_hash(prompt_path),
    }


def _find_matching_manifest(
    trade_date: date,
    expected: dict[str, str],
    *,
    input_count: int,
) -> dict[str, Any] | None:
    output_root = ROOT / "outputs" / "event_overlay" / trade_date.isoformat()
    candidates: list[tuple[float, dict[str, Any]]] = []
    for path in output_root.glob("*/run_manifest.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        source_artifact = manifest.get("source_artifact") or {}
        universe_artifact = manifest.get("universe_source_artifact") or {}
        matches = (
            manifest.get("final_status") == "V3_EVENT_OVERLAY_SHADOW_READY"
            and manifest.get("real_search_enabled") is True
            and int(manifest.get("input_count") or 0) == input_count
            and int(manifest.get("successful_evaluation_count") or 0) == input_count
            and int(manifest.get("search_failure_count") or 0) == 0
            and int(manifest.get("provider_failure_count") or 0) == 0
            and str(manifest.get("source_run_id") or "")
            == expected["source_run_id"]
            and str(source_artifact.get("sha256") or "")
            == expected["source_hash"]
            and str(universe_artifact.get("sha256") or "")
            == expected["universe_hash"]
            and str(manifest.get("prompt_hash") or "") == expected["prompt_hash"]
            and str(manifest.get("screening_version") or "") == SCREENING_VERSION
            and str(manifest.get("event_review_version") or "")
            == EVENT_REVIEW_VERSION
            and str(manifest.get("search_contract_version") or "")
            == SEARCH_CONTRACT_VERSION
            and str(manifest.get("checkpoint_contract_version") or "")
            == CHECKPOINT_CONTRACT_VERSION
            and (
                input_count != 100
                or manifest.get("database_publish_eligible") is True
            )
        )
        if not matches:
            continue
        manifest["_output_dir"] = str(path.parent.resolve())
        candidates.append((path.stat().st_mtime, manifest))
    return max(candidates, default=(0.0, None), key=lambda item: item[0])[1]


def _publish_full_v31_if_eligible(report: dict) -> dict:
    """Publish only a complete, database-eligible real Top100 result."""

    if report.get("status") != "V3_EVENT_OVERLAY_SHADOW_READY":
        return {"status": "SKIPPED", "reason": "RUN_NOT_READY"}
    output_dir = Path((report.get("artifacts") or {}).get("output_dir") or "")
    manifest_path = output_dir / "run_manifest.json"
    if not manifest_path.is_file():
        return {"status": "SKIPPED", "reason": "RUN_MANIFEST_MISSING"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("database_publish_eligible") is not True:
        return {
            "status": "SKIPPED",
            "reason": "NOT_DATABASE_PUBLISH_ELIGIBLE",
        }
    if int(manifest.get("input_count") or 0) != 100:
        return {"status": "SKIPPED", "reason": "FULL_TOP100_REQUIRED"}
    return publish_internal_web_snapshot()


if __name__ == "__main__":
    raise SystemExit(main())
