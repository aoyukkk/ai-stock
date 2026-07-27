from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
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
from post_close.official_run import OFFICIAL_SUCCESS, PostCloseOfficialRunner
from reporting.web_result_publish import publish_internal_web_snapshot
from reporting.workbook_style import WorkbookStyleService
from scripts.prewarm_tushare_trade_date_cache import _load_local_tushare_token
from scripts.run_daily_routine import _assert_advisory_only


SHANGHAI = ZoneInfo("Asia/Shanghai")
SKIP_SUCCESS = {"REUSED_EXISTING_OFFICIAL_RUN", "SKIPPED_NON_TRADING_DAY"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the reusable advisory-only official post-close workflow.")
    parser.add_argument("--trade-date", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dry-run-fixture-date", type=date.fromisoformat)
    parser.add_argument("--force-new-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    _assert_advisory_only()
    init_db()
    target_date = args.trade_date or datetime.now(SHANGHAI).date()

    if args.dry_run:
        fixture_date = args.dry_run_fixture_date or target_date
        reference = _reference_workbook(fixture_date)
        result = PostCloseOfficialRunner(ROOT, fixture_date, reference).dry_run()
        result["scheduled_trade_date"] = target_date.isoformat()
        result["calendar_refresh"] = {"attempted": False, "reason": "DRY_RUN_NO_EXTERNAL_CALLS"}
        _write_automation_audit(target_date, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") == "DRY_RUN_READY" else 2

    if not args.force_new_run:
        existing = _existing_success(target_date)
        if existing is not None:
            result = {
                "status": "REUSED_EXISTING_OFFICIAL_RUN",
                "trade_date": target_date.isoformat(),
                "run_id": existing.run_id,
                "final_status": existing.status,
                "output_paths": existing.output_paths_json or {},
                "real_orders": int(existing.real_orders or 0),
                "virtual_orders": int(existing.virtual_orders or 0),
                "scheduler": False,
            }
            result["web_sync"] = _publish_internal_web_snapshot()
            _write_automation_audit(target_date, result)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0 if result["web_sync"].get("status") != "FAILED" else 2

    calendar_status = _local_trade_day_status(target_date)
    calendar_refresh: dict[str, Any] = {"attempted": False, "reason": "LOCAL_CALENDAR_READY"}
    if calendar_status is None or _calendar_horizon_end() < target_date + timedelta(days=30):
        calendar_refresh = _refresh_calendar(target_date)
        calendar_status = _local_trade_day_status(target_date)
    if calendar_status is False:
        result = {
            "status": "SKIPPED_NON_TRADING_DAY",
            "trade_date": target_date.isoformat(),
            "calendar_refresh": calendar_refresh,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }
        _write_automation_audit(target_date, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    if calendar_status is None:
        result = {
            "status": "POSTCLOSE_INTEGRATION_FAILED",
            "trade_date": target_date.isoformat(),
            "error": "TRADE_CALENDAR_STATUS_UNKNOWN",
            "calendar_refresh": calendar_refresh,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }
        _write_automation_audit(target_date, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 2

    reference = _reference_workbook(target_date)
    result = PostCloseOfficialRunner(ROOT, target_date, reference).run()
    result["calendar_refresh"] = calendar_refresh
    result["web_sync"] = _publish_internal_web_snapshot()
    _write_automation_audit(target_date, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("final_status") in OFFICIAL_SUCCESS and result["web_sync"].get("status") != "FAILED" else 2


def _publish_internal_web_snapshot() -> dict[str, Any]:
    return publish_internal_web_snapshot()


def _reference_workbook(target_date: date) -> Path:
    return WorkbookStyleService.resolve_recent_successful_reference(
        ROOT / "outputs",
        start=target_date - timedelta(days=60),
        end=target_date - timedelta(days=1),
    )


def _existing_success(target_date: date) -> PostCloseOfficialRun | None:
    session = get_session()
    try:
        return session.scalar(
            select(PostCloseOfficialRun)
            .where(
                PostCloseOfficialRun.trade_date == target_date,
                PostCloseOfficialRun.status.in_(OFFICIAL_SUCCESS),
            )
            .order_by(PostCloseOfficialRun.completed_at.desc())
        )
    finally:
        session.close()


def _calendar_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cache_root = ROOT / "data" / "cache" / "tushare"
    for path in cache_root.glob("trade_cal_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, list):
            rows.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            values = payload.get("data") or payload.get("records") or payload.get("items") or []
            if isinstance(values, list):
                rows.extend(item for item in values if isinstance(item, dict))
    return rows


def _local_trade_day_status(target_date: date) -> bool | None:
    key = f"{target_date:%Y%m%d}"
    values = [row for row in _calendar_rows() if str(row.get("cal_date") or "") == key]
    if values:
        return any(str(row.get("is_open")).strip().lower() in {"1", "1.0", "true"} for row in values)
    daily = ROOT / "data" / "cache" / "tushare" / "trade_date" / "daily" / f"{key}.json"
    if daily.is_file():
        return True
    if target_date.weekday() >= 5:
        return False
    return None


def _calendar_horizon_end() -> date:
    dates = []
    for row in _calendar_rows():
        value = str(row.get("cal_date") or "")
        if len(value) == 8 and value.isdigit():
            dates.append(date(int(value[:4]), int(value[4:6]), int(value[6:8])))
    return max(dates, default=date.min)


def _refresh_calendar(target_date: date) -> dict[str, Any]:
    _load_local_tushare_token()
    started = datetime.now(SHANGHAI)
    try:
        provider = TushareMarketDataProvider(cache_enabled=True)
        result = provider.get_trade_calendar(
            start_date=(target_date - timedelta(days=14)).isoformat(),
            end_date=(target_date + timedelta(days=90)).isoformat(),
            is_open=None,
        )
        return {
            "attempted": True,
            "status": result.status,
            "row_count": len(result.records),
            "started_at": started.isoformat(),
            "finished_at": datetime.now(SHANGHAI).isoformat(),
            "error_type": result.error_type,
            "error_message": result.error_message,
        }
    except Exception as exc:
        return {
            "attempted": True,
            "status": "error",
            "row_count": 0,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(SHANGHAI).isoformat(),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }


def _write_automation_audit(target_date: date, payload: dict[str, Any]) -> Path:
    path = ROOT / "outputs" / target_date.isoformat() / "审计" / "每日自动化_盘后.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
