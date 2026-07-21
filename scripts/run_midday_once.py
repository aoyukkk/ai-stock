from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.config import get_app_config
from backend.core.runtime_paths import output_root
from database.session import get_session, init_db
from midday.core import MiddayBaselineResolver, MiddayTimeGate, SHANGHAI
from midday.llm_review import MiddayLLMReviewer
from midday.provider import MiddayIFindCollector
from midday.service import MiddayRecommendationService
from scripts.run_daily_routine import _assert_advisory_only, _run_midday


SUCCESS_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS", "WAITING_AFTERNOON_RECHECK"}


def main() -> int:
    parser = argparse.ArgumentParser(description="One-click advisory-only midday recommendation.")
    parser.add_argument("--trade-date", type=date.fromisoformat)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--max-wait-seconds", type=int, default=300)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    _assert_advisory_only()
    init_db()
    trade_date = args.trade_date or datetime.now(SHANGHAI).date()
    _wait_for_window(trade_date, max_wait_seconds=0 if args.no_wait else args.max_wait_seconds)
    preflight = _preflight(trade_date)
    if args.preflight_only or not preflight["ready"]:
        result = {"status": "PREFLIGHT_READY" if preflight["ready"] else "PREFLIGHT_BLOCKED", "preflight": preflight}
        _write_audit(trade_date, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if preflight["ready"] else 2

    try:
        result = _run_midday(trade_date, force=False)
        payload = {**result, "preflight": preflight}
    except Exception as exc:
        payload = {
            "status": "FAILED",
            "error_category": type(exc).__name__,
            "error": str(exc),
            "preflight": preflight,
            "orders_created": 0,
            "real_trading_enabled": False,
        }
    audit = _write_audit(trade_date, payload)
    payload["one_click_audit"] = str(audit)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload.get("status") in SUCCESS_STATUSES else 2


def _preflight(trade_date: date, *, now: datetime | None = None) -> dict[str, Any]:
    local = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    app_config = get_app_config()
    config = app_config.config_files.get("midday_recommendation", {}).get("midday_recommendation", {})
    time_gate = MiddayTimeGate(
        start_after=str(config.get("start_after", "11:32")),
        latest=str(config.get("latest_start_time", "12:50")),
    ).evaluate(trade_date, local)
    session = get_session()
    try:
        try:
            baseline = MiddayBaselineResolver(session).resolve(
                trade_date,
                top_n=int(config.get("pool", {}).get("base_top_n", 100)),
            )
            baseline_status = {
                "ready": True,
                "trade_date": baseline["trade_date"],
                "quant_run_id": baseline["quant_run_id"],
                "row_count": len(baseline["rows"]),
            }
        except Exception as exc:
            baseline_status = {"ready": False, "error_category": type(exc).__name__, "error": str(exc)}
        collector_gate = MiddayIFindCollector(session, app_config, config.get("ifind", {})).gate()
        llm_gate = MiddayLLMReviewer(session, config).gate()
        existing = MiddayRecommendationService(session, app_config).status(trade_date=trade_date)
    finally:
        session.close()

    ifind = config.get("ifind", {})
    fast_path = bool(ifind.get("fast_minutes_only", False))
    expected_calls = int(ifind.get("minute_max_stocks", 30)) if fast_path else (
        _ceil_div(int(ifind.get("index_max_codes", 8)), int(ifind.get("index_batch_size", 2)))
        + _ceil_div(int(ifind.get("snapshot_max_stocks", 120)), int(ifind.get("snapshot_batch_size", 4)))
        + int(ifind.get("minute_max_stocks", 30))
    )
    hard_limit = min(30, int(ifind.get("max_external_calls", 30)))
    call_budget = {"ready": expected_calls <= hard_limit, "expected_calls": expected_calls, "hard_limit": hard_limit, "fast_minutes_only": fast_path}
    already_complete = existing.get("status") in SUCCESS_STATUSES and bool(existing.get("excel_path")) and Path(str(existing["excel_path"])).is_file()
    gates_ready = bool(
        baseline_status["ready"]
        and collector_gate["passed"]
        and llm_gate["passed"]
        and call_budget["ready"]
        and not app_config.real_trading_enabled
    )
    return {
        "ready": bool(already_complete or (time_gate["passed"] and gates_ready)),
        "already_complete": already_complete,
        "trade_date": trade_date,
        "checked_at": local,
        "time_gate": time_gate,
        "baseline": baseline_status,
        "ifind_gate": collector_gate,
        "llm_gate": llm_gate,
        "call_budget": call_budget,
        "output_path": str(output_root() / trade_date.isoformat() / "午盘" / f"智能交易助手_午盘_{trade_date.isoformat()}.xlsx"),
        "real_trading_enabled": False,
        "orders_created": 0,
    }


def _wait_for_window(trade_date: date, *, max_wait_seconds: int) -> None:
    now = datetime.now(SHANGHAI)
    if now.date() != trade_date or max_wait_seconds <= 0:
        return
    target = now.replace(hour=11, minute=32, second=0, microsecond=0)
    wait_seconds = (target - now).total_seconds()
    if 0 < wait_seconds <= max_wait_seconds:
        print(f"Waiting {int(wait_seconds)} seconds for the 11:32 midday window...", flush=True)
        time.sleep(wait_seconds)


def _write_audit(trade_date: date, payload: dict[str, Any]) -> Path:
    path = output_root() / trade_date.isoformat() / "午盘" / "审计" / "午盘一键运行报告.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


if __name__ == "__main__":
    raise SystemExit(main())
