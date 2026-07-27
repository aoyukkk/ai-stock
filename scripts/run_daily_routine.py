from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import func, select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application.workflow import WorkflowApplicationService, execute_persisted_job
from backend.core.runtime_paths import output_root, tushare_cache_root
from backend.workbench.service import WorkbenchService
from database.models.midday import MiddayRecommendationRun
from database.models.quant_run import QuantRankResult, QuantRun
from database.models.stock import StockMaster
from database.models.validation import ModelValidationRun, ModelValidationSample, ProResumeRun
from database.models.workbench import ManualSelectionRecord, PipelineJob
from database.session import get_session, init_db
from market_review.service import MarketReviewService
from midday.excel import MiddayRecommendationExcelService
from midday.service import MiddayRecommendationService
from database.models.intraday_monitor import IntradayMonitorSession
from intraday_monitor.coordinator import MiddayCompatibilityGuard
from review.performance_schemas import PerformanceRequest
from review.selection_performance_service import SelectionPerformanceService
from reporting.web_result_publish import publish_internal_web_snapshot
from stock_codes import normalize_ts_code
from trader_demo.pro_single_v3 import SINGLE_CONTRACT_VERSION


STAGES = ("data", "quant", "manual", "flash", "pro", "market", "export", "monitor")


def main() -> int:
    parser = argparse.ArgumentParser(description="A股交易助手每日午盘与盘后常态化流程。")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "midday"):
        item = subparsers.add_parser(command)
        item.add_argument("--trade-date", required=True, type=date.fromisoformat)
    midday = subparsers.choices["midday"]
    midday.add_argument("--force", action="store_true")

    close = subparsers.add_parser("close")
    close.add_argument("--trade-date", required=True, type=date.fromisoformat)
    close.add_argument("--manual-pool", type=Path)
    close.add_argument("--start-stage", choices=STAGES, default="data")
    close.add_argument("--refresh-data", action="store_true")
    close.add_argument("--confirm-llm-budget", action="store_true")
    close.add_argument("--market-supplement", type=Path)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    _assert_advisory_only()
    init_db()
    if args.command == "status":
        print(json.dumps(_status(args.trade_date), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "midday":
        result = _run_midday(args.trade_date, force=args.force)
        result["web_sync"] = publish_internal_web_snapshot()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if (
            result.get("status")
            in {"SUCCESS", "PARTIAL_SUCCESS", "WAITING_AFTERNOON_RECHECK"}
            and result["web_sync"].get("status") != "FAILED"
        ) else 2
    result = _run_close(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _run_midday(trade_date: date, *, force: bool) -> dict[str, Any]:
    session = get_session()
    guard = MiddayCompatibilityGuard()
    try:
        monitor = session.scalar(select(IntradayMonitorSession).where(
            IntradayMonitorSession.trade_date == trade_date,
        ).order_by(IntradayMonitorSession.created_at.desc()))
        compatibility = guard.begin_midday(monitor)
        session.commit()
        service = MiddayRecommendationService(session)
        existing = service.status(trade_date=trade_date)
        if existing.get("status") in {"SUCCESS", "PARTIAL_SUCCESS", "WAITING_AFTERNOON_RECHECK"} and not force:
            run = existing
            reused = True
        else:
            run = service.run(trade_date, force=force)
            reused = False
        if run.get("status") not in {"SUCCESS", "PARTIAL_SUCCESS", "WAITING_AFTERNOON_RECHECK"}:
            return {**run, "reused": reused, "single_midday_run": True, "midday_compatibility": compatibility}
        existing_output = Path(str(run.get("excel_path") or ""))
        if reused and run.get("excel_path") and existing_output.is_file():
            return {
                **run,
                "reused": True,
                "single_midday_run": True,
                "output_path": str(existing_output),
                "workbook_validation": {"status": "REUSED_EXISTING_WORKBOOK"},
                "midday_compatibility": compatibility,
            }
        exported = MiddayRecommendationExcelService(session, output_root()).export(str(run["run_id"]))
        return {
            **run,
            "reused": reused,
            "single_midday_run": True,
            "output_path": exported["output_path"],
            "workbook_validation": exported["validation"],
            "midday_compatibility": compatibility,
        }
    finally:
        guard.end_midday()
        session.close()


def _run_close(args) -> dict[str, Any]:
    remaining_stages = STAGES[STAGES.index(args.start_stage):]
    if any(stage in {"flash", "pro"} for stage in remaining_stages) and not args.confirm_llm_budget:
        raise ValueError("LLM_BUDGET_CONFIRMATION_REQUIRED")
    report: dict[str, Any] = {
        "trade_date": args.trade_date.isoformat(),
        "start_stage": args.start_stage,
        "stages": {},
        "real_trading_enabled": False,
    }
    start_index = STAGES.index(args.start_stage)
    for stage in STAGES[start_index:]:
        if stage == "data":
            report["stages"][stage] = _ensure_data(args.trade_date, refresh=args.refresh_data)
        elif stage == "quant":
            report["stages"][stage] = _ensure_quant(args.trade_date)
        elif stage == "manual":
            report["stages"][stage] = _ensure_manual(args.trade_date, args.manual_pool)
        elif stage == "flash":
            report["stages"][stage] = _ensure_flash(args.trade_date)
        elif stage == "pro":
            report["stages"][stage] = _ensure_pro(args.trade_date)
        elif stage == "market":
            report["stages"][stage] = _ensure_market(args.trade_date)
        elif stage == "export":
            report["stages"][stage] = _export_daily(args.trade_date, args.market_supplement)
        elif stage == "monitor":
            report["stages"][stage] = _build_monitor(args.trade_date)
    report["web_sync"] = publish_internal_web_snapshot()
    audit = ROOT / "outputs" / args.trade_date.isoformat() / "审计" / "每日常态化流程报告.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report["audit_path"] = str(audit)
    return report


def _ensure_data(trade_date: date, *, refresh: bool) -> dict[str, Any]:
    cache = tushare_cache_root() / "trade_date" / "daily" / f"{trade_date:%Y%m%d}.json"
    if cache.is_file() and not refresh:
        return {"status": "REUSED", "cache": str(cache)}
    return _run_job("DATA", trade_date, {"force": refresh})


def _ensure_quant(trade_date: date) -> dict[str, Any]:
    session = get_session()
    recompute = False
    try:
        row = session.scalar(select(QuantRun).where(
            QuantRun.base_market_trade_date == trade_date,
            QuantRun.status == "COMPLETED",
            QuantRun.actionable.is_(True),
            QuantRun.no_llm_call_verified.is_(True),
        ).order_by(QuantRun.created_at.desc()))
        if row and _quant_coverage_is_usable(row):
            return {"status": "REUSED", "quant_run_id": row.run_id, "scored_count": row.scored_count}
        recompute = row is not None
    finally:
        session.close()
    return _run_job("QUANT", trade_date, {"force": False, "recompute": recompute})


def _quant_coverage_is_usable(row: QuantRun) -> bool:
    filtered = int(row.filtered_count or 0)
    scored = int(row.scored_count or 0)
    if filtered <= 0:
        return False
    return scored / filtered >= 0.95


def _ensure_manual(trade_date: date, pool_path: Path | None) -> dict[str, Any]:
    session = get_session()
    try:
        service = WorkbenchService(session)
        source = str(pool_path) if pool_path else "DATABASE"
        if pool_path:
            rows = json.loads(pool_path.read_text(encoding="utf-8"))
            if not isinstance(rows, list) or not rows:
                raise ValueError("MANUAL_POOL_FILE_EMPTY")
            service.clear_manual(trade_date)
            quant = session.scalar(select(QuantRun).where(
                QuantRun.base_market_trade_date == trade_date,
                QuantRun.status == "COMPLETED",
            ).order_by(QuantRun.created_at.desc()))
            for item in rows:
                code = normalize_ts_code(str(item["stock_code"]))
                master = session.scalar(select(StockMaster).where(StockMaster.code == code))
                if master is None or str(master.status or "").upper() not in {"L", "LISTED", "NORMAL"}:
                    raise ValueError(f"MANUAL_STOCK_NOT_LISTED:{item['stock_code']}")
                expected_name = str(item.get("stock_name") or "").strip()
                if expected_name and expected_name != master.name:
                    raise ValueError(f"MANUAL_STOCK_NAME_MISMATCH:{item['stock_code']}")
                reason = str(item.get("reason") or "人工关注")
                origin = str(item.get("origin_date") or "").strip()
                if origin:
                    reason = f"{reason}；原始加入日 {origin}"
                service.add_manual(
                    trade_date,
                    code,
                    reason,
                    str(item.get("priority") or "HIGH"),
                    quant.run_id if quant else None,
                )
        count = int(session.scalar(select(func.count()).select_from(ManualSelectionRecord).where(
            ManualSelectionRecord.trade_date == trade_date
        )) or 0)
        if count == 0 and not pool_path:
            carried = _carry_forward_manual_pool(session, service, trade_date)
            count = carried["count"]
            source = carried["source"]
        if count == 0:
            raise ValueError("MANUAL_POOL_REQUIRED")
        return {"status": "READY", "count": count, "source": source}
    finally:
        session.close()


def _carry_forward_manual_pool(session, service: WorkbenchService, trade_date: date) -> dict[str, Any]:
    previous_date = session.scalar(select(func.max(ManualSelectionRecord.trade_date)).where(
        ManualSelectionRecord.trade_date < trade_date
    ))
    if previous_date is None:
        return {"count": 0, "source": "NONE"}
    rows = list(session.scalars(select(ManualSelectionRecord).where(
        ManualSelectionRecord.trade_date == previous_date
    ).order_by(ManualSelectionRecord.created_at)))
    quant = _latest_actionable_quant(session, trade_date)
    for row in rows:
        service.add_manual(
            trade_date,
            row.stock_code,
            row.reason,
            row.priority,
            quant.run_id if quant else None,
        )
    return {"count": len(rows), "source": f"CARRIED_FORWARD:{previous_date.isoformat()}"}


def _ensure_flash(trade_date: date) -> dict[str, Any]:
    session = get_session()
    try:
        quant = _latest_actionable_quant(session, trade_date)
        requested_manual_codes = _manual_codes(session, trade_date)
        manual_codes = (
            _eligible_manual_codes(
                session,
                trade_date,
                quant.run_id,
                requested_manual_codes,
            )
            if quant
            else []
        )
        row = _latest_usable_flash(session, trade_date, quant.run_id, manual_codes) if quant else None
        if row:
            return {
                "status": "REUSED",
                "flash_run_id": row.run_id,
                "quant_run_id": quant.run_id,
                "manual_hash": _hash_codes(manual_codes),
                "manual_requested_count": len(requested_manual_codes),
                "manual_included_count": len(manual_codes),
                "manual_excluded": sorted(set(requested_manual_codes) - set(manual_codes)),
            }
    finally:
        session.close()
    if quant is None:
        raise ValueError("ACTIONABLE_QUANT_RUN_REQUIRED")
    return _run_job("FLASH", trade_date, {
        "confirm_budget": True,
        "force": False,
        "quant_run_id": quant.run_id,
        "manual_hash": _hash_codes(manual_codes),
    })


def _ensure_pro(trade_date: date) -> dict[str, Any]:
    session = get_session()
    try:
        quant = _latest_actionable_quant(session, trade_date)
        requested_manual_codes = _manual_codes(session, trade_date)
        manual_codes = (
            _eligible_manual_codes(
                session,
                trade_date,
                quant.run_id,
                requested_manual_codes,
            )
            if quant
            else []
        )
        flash = _latest_usable_flash(session, trade_date, quant.run_id, manual_codes) if quant else None
        if flash is None:
            raise ValueError("CURRENT_FLASH_RUN_REQUIRED")
        row = session.scalar(select(ProResumeRun).where(
            ProResumeRun.base_trade_date == trade_date,
            ProResumeRun.flash_validation_run_id == flash.run_id,
            ProResumeRun.pro_contract_version == SINGLE_CONTRACT_VERSION,
            ProResumeRun.status == "COMPLETED",
        ).order_by(ProResumeRun.created_at.desc()))
        if row:
            return {
                "status": "REUSED",
                "pro_run_id": row.run_id,
                "candidate_count": row.candidate_count,
                "manual_requested_count": len(requested_manual_codes),
                "manual_included_count": len(manual_codes),
                "manual_excluded": sorted(
                    set(requested_manual_codes) - set(manual_codes)
                ),
            }
    finally:
        session.close()
    return _run_job("FINAL", trade_date, {
        "confirm_budget": True,
        "force": False,
        "flash_run_id": flash.run_id,
        "contract_version": SINGLE_CONTRACT_VERSION,
    })


def _ensure_market(trade_date: date) -> dict[str, Any]:
    session = get_session()
    try:
        service = MarketReviewService(session)
        existing = service.latest(trade_date)
        bundle = existing or service.run(trade_date, mode="DATA_ONLY")
        return {"status": bundle["run"]["status"], "run_id": bundle["run"]["run_id"]}
    finally:
        session.close()


def _export_daily(trade_date: date, supplement: Path | None) -> dict[str, Any]:
    session = get_session()
    try:
        pro = session.scalar(select(ProResumeRun).where(
            ProResumeRun.base_trade_date == trade_date,
            ProResumeRun.status == "COMPLETED",
        ).order_by(ProResumeRun.created_at.desc()))
        if pro is None:
            raise ValueError("COMPLETED_PRO_RUN_REQUIRED")
        flash_run_id = pro.flash_validation_run_id
        market = MarketReviewService(session).latest(trade_date)
    finally:
        session.close()
    daily_result = _checked_json_command([
        sys.executable,
        str(ROOT / "scripts" / "build_human_daily_output.py"),
        "--date", trade_date.isoformat(),
        "--validation-run", flash_run_id,
    ])
    market_output = None
    if market:
        command = [
            sys.executable,
            str(ROOT / "scripts" / "build_human_market_review.py"),
            "--trade-date", trade_date.isoformat(),
            "--run-id", str(market["run"]["run_id"]),
        ]
        if supplement and supplement.is_file():
            command.extend(["--supplement", str(supplement.resolve())])
        market_result = _checked_json_command(command)
        market_output = Path(str(market_result["output"]))
    return {
        "status": "SUCCESS",
        "daily_workbook": str(daily_result["output_path"]),
        "market_workbook": str(market_output) if market_output else None,
    }


def _build_monitor(trade_date: date) -> dict[str, Any]:
    trading_days = _recent_trading_days(trade_date, count=7)
    session = get_session()
    run_ids: dict[str, str] = {}
    try:
        service = SelectionPerformanceService(session)
        for key, scope in (("today_recommendation", "FINAL_CANDIDATES"), ("key_candidates", "KEY_CANDIDATES")):
            request = PerformanceRequest(
                evaluation_end_date=trade_date,
                lookback_value=7,
                lookback_unit="CUSTOM",
                start_selection_date=trading_days[0],
                end_selection_date=trading_days[-1],
                return_basis="SIGNAL_CLOSE",
                selection_scope=scope,
                weighting_mode="EQUAL_WEIGHT",
                include_zero_position_stocks=True,
                include_risk_blocked_stocks=True,
            )
            started = service.start(request)
            run_id = str(started.get("performance_run_id") or started.get("existing_run_id"))
            if started.get("job_id"):
                service.execute(run_id, str(started["job_id"]))
            run_ids[key] = run_id
    finally:
        session.close()
    outputs = {
        "today_recommendation": ROOT / "outputs" / trade_date.isoformat() / "复盘" / f"今日推荐复盘_截至{trade_date.isoformat()}.xlsx",
        "key_candidates": ROOT / "outputs" / trade_date.isoformat() / "复盘" / f"重点候选复盘_截至{trade_date.isoformat()}.xlsx",
    }
    for key, report_kind in (("today_recommendation", "today-recommendation"), ("key_candidates", "key-candidates")):
        _checked_command([
            sys.executable,
            str(ROOT / "scripts" / "build_selection_review_comparison.py"),
            "--performance-run-id", run_ids[key],
            "--evaluation-end-date", trade_date.isoformat(),
            "--report-kind", report_kind,
            "--output", str(outputs[key]),
        ])
    return {
        "status": "SUCCESS",
        "trading_days": [value.isoformat() for value in trading_days],
        "selection_window": {
            "start": trading_days[0].isoformat(),
            "end": trading_days[-1].isoformat(),
        },
        "performance_run_ids": run_ids,
        "today_recommendation_output": str(outputs["today_recommendation"]),
        "key_candidates_output": str(outputs["key_candidates"]),
    }


def _recent_trading_days(end_date: date, *, count: int) -> list[date]:
    values: set[date] = set()
    for path in tushare_cache_root().glob("trade_cal_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        rows = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
        for row in rows:
            if not isinstance(row, dict) or str(row.get("is_open")) not in {"1", "True", "true"}:
                continue
            raw = str(row.get("cal_date") or "").strip()
            try:
                value = date.fromisoformat(raw) if "-" in raw else date(
                    int(raw[:4]), int(raw[4:6]), int(raw[6:8])
                )
            except (TypeError, ValueError):
                continue
            if value <= end_date:
                values.add(value)
    daily_path = tushare_cache_root() / "trade_date" / "daily" / f"{end_date:%Y%m%d}.json"
    if daily_path.is_file():
        values.add(end_date)
    selected = sorted(values)[-count:]
    if len(selected) != count or selected[-1] != end_date:
        raise ValueError(f"RECENT_TRADING_DAY_CALENDAR_INCOMPLETE:{end_date.isoformat()}:{count}")
    return selected


def _run_job(job_type: str, trade_date: date, options: dict[str, Any]) -> dict[str, Any]:
    session = get_session()
    try:
        started = WorkflowApplicationService(session).start(job_type, trade_date, options)
    finally:
        session.close()
    if started.get("duplicate_status") == "NEW_JOB":
        execute_persisted_job(str(started["job_id"]))
    session = get_session()
    try:
        row = session.scalar(select(PipelineJob).where(PipelineJob.job_id == started["job_id"]))
        if row is None or row.status != "SUCCESS":
            raise RuntimeError(f"{job_type}_FAILED:{getattr(row, 'error_code', 'UNKNOWN')}")
        return {"status": row.status, "job_id": row.job_id, "run_ids": row.run_ids}
    finally:
        session.close()


def _status(trade_date: date) -> dict[str, Any]:
    session = get_session()
    try:
        workbench = WorkbenchService(session).status(trade_date)
        midday = MiddayRecommendationService(session).status(trade_date=trade_date)
        return {
            "trade_date": trade_date.isoformat(),
            "midday": midday,
            "pipeline": workbench.get("latest_run_ids"),
            "stages": {key: workbench.get(key) for key in ("data", "quant", "flash", "manual", "final", "market_review", "export")},
            "real_trading_enabled": False,
        }
    finally:
        session.close()


def _usable_flash(row: ModelValidationRun) -> bool:
    quality = dict((row.config_snapshot or {}).get("flash_batch_quality") or {})
    return bool(quality and not quality.get("degenerate") and quality.get("usable_for_final", True))


def _latest_actionable_quant(session, trade_date: date) -> QuantRun | None:
    return session.scalar(select(QuantRun).where(
        QuantRun.base_market_trade_date == trade_date,
        QuantRun.status == "COMPLETED",
        QuantRun.actionable.is_(True),
        QuantRun.no_llm_call_verified.is_(True),
    ).order_by(QuantRun.created_at.desc()))


def _manual_codes(session, trade_date: date) -> list[str]:
    return sorted({
        normalize_ts_code(code)
        for code in session.scalars(select(ManualSelectionRecord.stock_code).where(
            ManualSelectionRecord.trade_date == trade_date
        ))
    })


def _eligible_manual_codes(
    session,
    trade_date: date,
    quant_run_id: str,
    requested_codes: list[str] | None = None,
) -> list[str]:
    requested = requested_codes if requested_codes is not None else _manual_codes(
        session,
        trade_date,
    )
    quant_codes = {
        str(code or "").strip().upper().split(".", 1)[0]
        for code in session.scalars(
            select(QuantRankResult.stock_code).where(
                QuantRankResult.quant_run_id == quant_run_id
            )
        )
    }
    return [
        code
        for code in requested
        if str(code or "").strip().upper().split(".", 1)[0] in quant_codes
    ]


def _latest_usable_flash(
    session,
    trade_date: date,
    quant_run_id: str,
    manual_codes: list[str],
) -> ModelValidationRun | None:
    rows = list(session.scalars(select(ModelValidationRun).where(
        ModelValidationRun.base_market_trade_date == trade_date,
        ModelValidationRun.quant_run_id == quant_run_id,
        ModelValidationRun.real_llm.is_(True),
        ModelValidationRun.status.in_(["COMPLETED", "SUCCESS", "PARTIAL_SUCCESS"]),
    ).order_by(ModelValidationRun.created_at.desc())))
    expected = set(manual_codes)
    for row in rows:
        if not _usable_flash(row):
            continue
        samples = list(session.scalars(select(ModelValidationSample).where(
            ModelValidationSample.validation_run_id == row.run_id
        )))
        actual = {
            normalize_ts_code(sample.stock_code)
            for sample in samples
            if bool(((sample.screening_result or {}).get("_trader_demo") or {}).get("manual_selected"))
        }
        if actual == expected:
            return row
    return None


def _hash_codes(codes: list[str]) -> str:
    payload = json.dumps(codes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _checked_command(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT, check=False, env=_utf8_subprocess_env())
    if result.returncode != 0:
        raise RuntimeError(f"DAILY_ROUTINE_COMMAND_FAILED:{Path(command[1]).name}:{result.returncode}")


def _checked_json_command(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_utf8_subprocess_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"DAILY_ROUTINE_COMMAND_FAILED:{Path(command[1]).name}:{result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DAILY_ROUTINE_INVALID_JSON:{Path(command[1]).name}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"DAILY_ROUTINE_INVALID_JSON:{Path(command[1]).name}")
    return payload


def _utf8_subprocess_env() -> dict[str, str]:
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _assert_advisory_only() -> None:
    import os

    if os.getenv("ENABLE_REAL_TRADING", "false").strip().lower() != "false":
        raise RuntimeError("ENABLE_REAL_TRADING_MUST_REMAIN_FALSE")


if __name__ == "__main__":
    raise SystemExit(main())
