from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.postclose_official import PostCloseOfficialRun
from database.session import get_session, init_db
from datasource.tushare_provider import TushareMarketDataProvider
from quant.shadow.tushare_quant_v2 import FACTOR_VERSION
from reporting.web_result_publish import publish_internal_web_snapshot
from scripts.build_v2_corrected_human_daily_output import build as build_v2_workbook
from scripts.finalize_monday_v2_shadow import (
    configure_runtime as configure_finalization,
    finalize,
)
from scripts.refill_v2_fundamentals import run as refill_fundamentals
from scripts.run_daily_routine import _assert_advisory_only
from scripts.run_tushare_quant_v2_validation import (
    configure_runtime as configure_quant,
    read_json,
    run as run_quant_v2,
)
from scripts.run_v3_event_overlay_shadow_once import run_daily_v31


SHANGHAI = ZoneInfo("Asia/Shanghai")
SUCCESS = {"POSTCLOSE_FULL_A_SUCCESS", "POSTCLOSE_FULL_A_PARTIAL_SUCCESS"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-click V2 official plus V3.1 Shadow advisory workflow."
    )
    parser.add_argument("--trade-date", type=date.fromisoformat)
    parser.add_argument("--target-trade-date", type=date.fromisoformat)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-ifind", action="store_true")
    parser.add_argument(
        "--skip-event-overlay",
        action="store_true",
        help="Explicit recovery override; normal daily runs must keep V3.1 enabled.",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    _assert_advisory_only()
    init_db()
    trade_date = args.trade_date or datetime.now(SHANGHAI).date()
    target_trade_date = args.target_trade_date or _next_trade_date(trade_date)

    existing = _existing_v2_official(trade_date)
    if existing and not args.force:
        event_overlay = (
            {"status": "EXPLICITLY_SKIPPED"}
            if args.skip_event_overlay
            else run_daily_v31(trade_date)
        )
        sync = (
            publish_internal_web_snapshot()
            if args.skip_event_overlay
            else event_overlay["web_sync"]
        )
        payload = {
            "status": "REUSED_EXISTING_V2_OFFICIAL_RUN",
            "trade_date": trade_date.isoformat(),
            "target_trade_date": target_trade_date.isoformat(),
            "run_id": existing.run_id,
            "factor_version": FACTOR_VERSION,
            "output_paths": existing.output_paths_json,
            "event_overlay_v3_1": event_overlay,
            "web_sync": sync,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if sync.get("status") != "FAILED" else 2

    _ensure_legacy_universe_report(trade_date)
    configure_quant(trade_date.isoformat(), target_trade_date.isoformat())
    quant_root = ROOT / "outputs" / "quant_v2_validation" / trade_date.isoformat()
    quant_report = read_json(quant_root / "quant_v2_validation.json", {})
    if (
        args.force
        or quant_report.get("trade_date") != trade_date.isoformat()
        or quant_report.get("final_status") != "MONDAY_SHADOW_PREDICTION_READY"
    ):
        quant_report = run_quant_v2(skip_ifind=args.skip_ifind)
    if quant_report.get("final_status") != "MONDAY_SHADOW_PREDICTION_READY":
        raise RuntimeError("V2_QUANT_NOT_READY")

    configure_finalization(
        trade_date.isoformat(),
        target_trade_date.isoformat(),
        base_run_id=str(quant_report["run_id"]),
    )
    final_path = quant_root / "monday_v2_candidate_audit.json"
    final_audit = read_json(final_path, {})
    if (
        args.force
        or final_audit.get("base_run_id") != quant_report["run_id"]
        or final_audit.get("final_status")
        not in {"MONDAY_V2_SHADOW_FINALIZED", "MONDAY_WATCH_POOL_ONLY"}
    ):
        final_audit = finalize(audit_only=False)

    fundamental_path = (
        ROOT
        / "outputs"
        / trade_date.isoformat()
        / "审计"
        / f"基本面LLM补全_V2_{trade_date:%Y%m%d}.json"
    )
    fundamental = read_json(fundamental_path, {})
    if (
        args.force
        or fundamental.get("trade_date") != trade_date.isoformat()
        or fundamental.get("status") != "COMPLETE"
    ):
        fundamental = refill_fundamentals(trade_date, output=fundamental_path)
    if fundamental.get("status") != "COMPLETE":
        raise RuntimeError("V2_FUNDAMENTAL_REFILL_NOT_COMPLETE")

    workbook = build_v2_workbook(
        trade_date,
        overwrite=True,
        fundamental_overlay=fundamental_path,
        web_overlay=None,
    )
    official = _register_official(
        trade_date=trade_date,
        target_trade_date=target_trade_date,
        quant_report=quant_report,
        final_audit=final_audit,
        fundamental=fundamental,
        workbook=workbook,
    )
    event_overlay = (
        {"status": "EXPLICITLY_SKIPPED"}
        if args.skip_event_overlay
        else run_daily_v31(
            trade_date,
            base_quant_run_id=str(quant_report["run_id"]),
        )
    )
    sync = (
        publish_internal_web_snapshot()
        if args.skip_event_overlay
        else event_overlay["web_sync"]
    )
    payload = {
        "status": official.status,
        "trade_date": trade_date.isoformat(),
        "target_trade_date": target_trade_date.isoformat(),
        "run_id": official.run_id,
        "factor_version": FACTOR_VERSION,
        "quant_run_id": quant_report["run_id"],
        "flash_calls": (final_audit.get("flash") or {}).get("api_calls", 0),
        "pro_calls": (final_audit.get("pro") or {}).get("api_calls", 0),
        "fundamental_calls": fundamental.get("actual_network_calls", 0),
        "event_overlay_v3_1": event_overlay,
        "output_paths": official.output_paths_json,
        "web_sync": sync,
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if official.status in SUCCESS and sync.get("status") != "FAILED" else 2


def _ensure_legacy_universe_report(trade_date: date) -> None:
    # The listed-stock universe changes independently of trade-date datasets.
    # Refresh it once before resolving/reusing the baseline report so newly
    # listed securities cannot be silently omitted by a stale stock_basic cache.
    provider = TushareMarketDataProvider(cache_enabled=True)
    stocks = provider.get_stock_list(use_cache=False)
    if not stocks:
        raise RuntimeError("FRESH_STOCK_BASIC_UNIVERSE_EMPTY")
    key = trade_date.strftime("%Y%m%d")
    if any((ROOT / "data" / "reports").glob(f"quant_{key}_*.json")):
        return
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_postclose_official_once.py"),
        "--trade-date",
        trade_date.isoformat(),
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise RuntimeError("V2_BASELINE_UNIVERSE_PREPARATION_FAILED")
    if not any((ROOT / "data" / "reports").glob(f"quant_{key}_*.json")):
        raise RuntimeError("V2_BASELINE_UNIVERSE_REPORT_MISSING_AFTER_PREPARATION")


def _next_trade_date(trade_date: date) -> date:
    cache = ROOT / "data" / "cache" / "tushare"
    values: set[date] = set()
    for path in cache.glob("trade_cal_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        for row in rows if isinstance(rows, list) else []:
            value = str(row.get("cal_date") or "")
            if (
                len(value) == 8
                and value.isdigit()
                and str(row.get("is_open")).strip().lower()
                in {"1", "1.0", "true"}
            ):
                values.add(date(int(value[:4]), int(value[4:6]), int(value[6:8])))
    future = sorted(value for value in values if value > trade_date)
    if not future:
        raise RuntimeError("NEXT_TRADE_DATE_NOT_AVAILABLE")
    return future[0]


def _existing_v2_official(trade_date: date) -> PostCloseOfficialRun | None:
    session = get_session()
    try:
        rows = list(
            session.scalars(
                select(PostCloseOfficialRun)
                .where(
                    PostCloseOfficialRun.trade_date == trade_date,
                    PostCloseOfficialRun.status.in_(SUCCESS),
                )
                .order_by(PostCloseOfficialRun.completed_at.desc())
            )
        )
        for row in rows:
            report = row.report_json or {}
            outputs = row.output_paths_json or {}
            workbook = Path(str(outputs.get("main_workbook") or ""))
            expected_sha256 = str(outputs.get("main_workbook_sha256") or "")
            actual_sha256 = (
                hashlib.sha256(workbook.read_bytes()).hexdigest()
                if workbook.is_file()
                else ""
            )
            if (
                report.get("factor_version") == FACTOR_VERSION
                and expected_sha256
                and actual_sha256 == expected_sha256
            ):
                return row
        return None
    finally:
        session.close()


def _register_official(
    *,
    trade_date: date,
    target_trade_date: date,
    quant_report: dict[str, Any],
    final_audit: dict[str, Any],
    fundamental: dict[str, Any],
    workbook: dict[str, Any],
) -> PostCloseOfficialRun:
    now = datetime.now(timezone.utc)
    run_id = f"postclose-v2-{trade_date:%Y%m%d}-{str(quant_report['run_id']).rsplit('-', 1)[-1]}"
    output_paths = {
        "main_workbook": workbook["formal_workbook"],
        "main_workbook_sha256": workbook["workbook_hashes"]["file_sha256"],
        "main_workbook_content_hash": workbook["workbook_hashes"]["content_hash"],
        "main_workbook_style_hash": workbook["workbook_hashes"]["style_hash"],
        "v2_quant_report": str(
            ROOT
            / "outputs"
            / "quant_v2_validation"
            / trade_date.isoformat()
            / "quant_v2_validation.json"
        ),
        "v2_final_audit": str(
            ROOT
            / "outputs"
            / "quant_v2_validation"
            / trade_date.isoformat()
            / "monday_v2_candidate_audit.json"
        ),
        "fundamental_overlay": str(
            ROOT
            / "outputs"
            / trade_date.isoformat()
            / "审计"
            / f"基本面LLM补全_V2_{trade_date:%Y%m%d}.json"
        ),
    }
    report = {
        "phase": f"{trade_date.isoformat()} V2 Official Post-Close",
        "factor_version": FACTOR_VERSION,
        "production_route": "V2_ONLY",
        "trade_date": trade_date.isoformat(),
        "target_trade_date": target_trade_date.isoformat(),
        "quant_run_id": quant_report["run_id"],
        "quant_status": quant_report["final_status"],
        "finalization_status": final_audit["final_status"],
        "flash": {
            "business_inputs": (final_audit.get("flash") or {}).get("business_inputs", 0),
            "successful": (final_audit.get("flash") or {}).get("successful", 0),
            "failed": (final_audit.get("flash") or {}).get("failed", 0),
            "api_calls": (final_audit.get("flash") or {}).get("api_calls", 0),
        },
        "pro": {
            "successful": len((final_audit.get("pro") or {}).get("results") or []),
            "api_calls": (final_audit.get("pro") or {}).get("api_calls", 0),
        },
        "fundamental": {
            "completed": fundamental.get("completed_count", 0),
            "failed": fundamental.get("failed_count", 0),
            "api_calls": fundamental.get("actual_network_calls", 0),
        },
        "active_candidates": final_audit.get("active_shadow") or [],
        "watch_pool": final_audit.get("watch_pool") or [],
        "output_paths": output_paths,
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
        "v1_workbook_deleted": True,
    }
    llm_calls = (
        int(report["flash"]["api_calls"] or 0)
        + int(report["pro"]["api_calls"] or 0)
        + int(report["fundamental"]["api_calls"] or 0)
    )
    total_tokens = (
        int((final_audit.get("flash") or {}).get("input_tokens") or 0)
        + int((final_audit.get("flash") or {}).get("output_tokens") or 0)
        + int((final_audit.get("pro") or {}).get("input_tokens") or 0)
        + int((final_audit.get("pro") or {}).get("output_tokens") or 0)
        + int(fundamental.get("input_tokens") or 0)
        + int(fundamental.get("output_tokens") or 0)
    )
    session = get_session()
    try:
        row = session.scalar(
            select(PostCloseOfficialRun).where(PostCloseOfficialRun.run_id == run_id)
        )
        if row is None:
            row = PostCloseOfficialRun(
                run_id=run_id,
                trade_date=trade_date,
                status="POSTCLOSE_FULL_A_SUCCESS",
                stage="COMPLETED",
                started_at=now,
                completed_at=now,
                report_json=report,
                output_paths_json=output_paths,
                provider_calls=int(
                    (quant_report.get("ifind_probe") or {}).get("http_call_count") or 0
                ),
                llm_calls=llm_calls,
                total_tokens=total_tokens,
                per_stock_api_calls=0,
                real_orders=0,
                virtual_orders=0,
                scheduler_enabled=False,
                production_config_changed=True,
            )
            session.add(row)
        else:
            row.report_json = report
            row.output_paths_json = output_paths
            row.completed_at = now
        session.commit()
        return row
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
