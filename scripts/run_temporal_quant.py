from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path: sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env", override=False)

from database.session import get_session, init_db
from quant.run_repository import QuantRunRepository
from scripts.run_real_quant_top500 import run_real_quant_top500
from temporal.readiness import DataReadinessService
from temporal.schemas import RunMode, TemporalStatus


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-mode", choices=[item.value for item in RunMode], required=True)
    parser.add_argument("--decision-time", required=True)
    parser.add_argument("--base-trade-date", required=True)
    parser.add_argument("--target-trade-date", required=True)
    parser.add_argument("--top-n", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    mode = RunMode(args.run_mode); decision = datetime.fromisoformat(args.decision_time)
    base = date.fromisoformat(args.base_trade_date); target = date.fromisoformat(args.target_trade_date)
    service = DataReadinessService(); context, manifest = service.check(
        mode, decision, base_market_trade_date=base, target_trade_date=target,
    )
    if args.dry_run or manifest.temporal_status is TemporalStatus.BLOCKED or not manifest.actionable:
        print(json.dumps(service.payload(context, manifest), ensure_ascii=False, indent=2, default=str))
        return 0 if args.dry_run else 2
    report = run_real_quant_top500(
        provider="tushare", history_provider="tushare", backup_history_provider="baostock",
        top_n=args.top_n, trade_date=base.isoformat(), end_date=base.isoformat(),
        save_to_db=True, use_cache=True, refresh_cache=False, progress=True,
        output="data/reports/temporal_quant_top500_report.json",
    )
    init_db(); session = get_session()
    try:
        run = QuantRunRepository(session).save_report(
            report, run_mode=mode.value, decision_time=context.decision_time,
            base_trade_date=base, target_trade_date=target, manifest_id=manifest.id,
            temporal_status=manifest.temporal_status.value, actionable=manifest.actionable,
            config_snapshot={"top_n": args.top_n, "provider": "tushare", "history_provider": "tushare"},
        )
        payload = {
            "run_id": run.run_id, "run_mode": run.run_mode, "decision_time": run.decision_time,
            "base_market_trade_date": run.base_market_trade_date, "target_trade_date": run.target_trade_date,
            "temporal_status": run.temporal_status, "universe_count": run.universe_count,
            "filtered_count": run.filtered_count, "scored_count": run.scored_count,
            "skipped_count": run.skipped_count, "top_count": run.top_count,
            "total_seconds": str(run.total_seconds), "trade_date_cache_used": run.trade_date_cache_used,
            "per_stock_api_call_count": run.per_stock_api_call_count,
            "no_llm_call_verified": run.no_llm_call_verified, "data_manifest_id": run.data_manifest_id,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    finally: session.close()


if __name__ == "__main__": raise SystemExit(main())
