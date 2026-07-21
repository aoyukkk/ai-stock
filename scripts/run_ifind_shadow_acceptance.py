from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.config import get_app_config
from database.session import get_session, init_db
from services.ifind_shadow_acceptance_service import IFindShadowAcceptanceService


def main() -> int:
    args = parse_args()
    load_dotenv(ROOT / ".env", override=False)
    original = {key: os.environ.get(key) for key in ("IFIND_HTTP_ENABLED", "RUN_REAL_IFIND_SHADOW")}
    try:
        if args.real_ifind:
            os.environ["IFIND_HTTP_ENABLED"] = "true"
            os.environ["RUN_REAL_IFIND_SHADOW"] = "true"
        app_config = get_app_config()
        trade_date = resolve_trade_date(args.trade_date, app_config.root_dir)
        if not tushare_daily_ready(trade_date, app_config.root_dir):
            print(json.dumps({"status": "BLOCKED", "reason": "TUSHARE_DAILY_DATA_NOT_READY", "trade_date": trade_date.isoformat()}, ensure_ascii=True))
            return 2
        init_db()
        session = get_session()
        try:
            result = IFindShadowAcceptanceService(session, app_config=app_config).run(
                mode=args.mode,
                trade_date=trade_date,
                pipeline_run_id=None if args.pipeline_run == "latest-compatible" else args.pipeline_run,
                stock_limit=args.stock_limit,
                minute_stock_count=args.minute_stock_count,
                rounds=args.rounds,
                interval_seconds=args.interval_seconds,
                max_external_calls=args.max_external_calls,
                force_provider_refresh=args.force_provider_refresh,
                output_dir=Path(args.output_dir).resolve() if args.output_dir else None,
            )
        finally:
            session.close()
        print(json.dumps({"status": result.get("status"), "acceptance_run_id": result.get("acceptance_run_id"), "report_path": result.get("report_path"), "external_call_count": result.get("external_call_count", 0), "auth_call_count": result.get("auth_call_count", 0), "blocked_reasons": result.get("blocked_reasons", [])}, ensure_ascii=True))
        return 0 if result.get("status") in {"CLOSED_SESSION_ACCEPTED", "OPEN_SESSION_ACCEPTED", "WAITING_FOR_OPEN_SESSION_ACCEPTANCE"} else 2
    finally:
        for key, value in original.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a guarded iFinD P0 live Shadow acceptance")
    parser.add_argument("--mode", choices=("closed-session", "open-session"), required=True)
    parser.add_argument("--trade-date", default="latest-completed")
    parser.add_argument("--pipeline-run", default="latest-compatible")
    parser.add_argument("--stock-limit", type=int, default=20)
    parser.add_argument("--minute-stock-count", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--max-external-calls", type=int, default=20)
    parser.add_argument("--force-provider-refresh", action="store_true")
    parser.add_argument("--output-dir", default="data/reports/ifind/acceptance")
    parser.add_argument("--real-ifind", action="store_true", help="Temporarily enable the explicit live Shadow gate for this process only")
    return parser.parse_args()


def resolve_trade_date(value: str, root: Path) -> date:
    if value != "latest-completed":
        return date.fromisoformat(value)
    cache_dir = root / "data" / "cache" / "tushare" / "trade_date" / "daily"
    candidates = [date.fromisoformat(path.stem[:4] + "-" + path.stem[4:6] + "-" + path.stem[6:8]) for path in cache_dir.glob("*.json") if len(path.stem) == 8 and path.stem.isdigit()]
    if not candidates:
        raise SystemExit("No cached Tushare daily trade date found")
    return max(candidates)


def tushare_daily_ready(trade_date: date, root: Path) -> bool:
    path = root / "data" / "cache" / "tushare" / "trade_date" / "daily" / f"{trade_date:%Y%m%d}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(payload, list) and bool(payload) and all(isinstance(row, dict) and row.get("ts_code") and row.get("trade_date") for row in payload[:10])
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
