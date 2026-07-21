from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import func, select

from database.models.system import LLMUsage
from database.session import get_database_identity, get_session, init_db
from market_review.service import MarketReviewService


def run_data_only(trade_date: date, output: Path, *, force: bool = False) -> dict:
    init_db()
    session = get_session()
    try:
        before = int(session.scalar(select(func.count()).select_from(LLMUsage)) or 0)
        result = MarketReviewService(session).run(trade_date, mode="DATA_ONLY", force=force)
        after = int(session.scalar(select(func.count()).select_from(LLMUsage)) or 0)
        report = {
            "phase": "Daily A-Share Market Review V1",
            "trade_date": trade_date.isoformat(),
            "mode": "DATA_ONLY",
            "database": get_database_identity(),
            "run": result["run"],
            "snapshot_summary": {
                "market_direction": result["snapshot"]["market_direction"],
                "market_regime": result["run"]["market_regime"],
                "data_quality_score": result["snapshot"]["data_quality_score"],
                "valid_stock_count": result["snapshot"]["breadth"].get("valid_count"),
                "index_available_count": result["snapshot"]["technical"].get("index_available_count"),
                "dataset_watermark_hash": result["snapshot"]["dataset_watermark_hash"],
                "snapshot_hash": result["snapshot"]["snapshot_hash"],
            },
            "outlook": result["outlook"],
            "search": result["search"],
            "external_search_calls": bool(result["search"].get("external_call_occurred")),
            "llm_calls": after - before,
            "quant_rerun": False,
            "flash_rerun": False,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return report
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local-data-only A-share daily market review.")
    parser.add_argument("--trade-date", required=True, type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = args.output or Path("data/reports") / f"market_daily_review_{args.trade_date:%Y%m%d}_data_only.json"
    report = run_data_only(args.trade_date, output, force=args.force)
    run = report["run"]
    snapshot = report["snapshot_summary"]
    print(json.dumps({
        "report": str(output), "run_id": run["run_id"], "status": run["status"],
        "market_direction": run["market_direction"], "market_regime": run["market_regime"],
        "data_quality_score": snapshot["data_quality_score"], "valid_stock_count": snapshot["valid_stock_count"],
        "index_available_count": snapshot["index_available_count"], "search_status": run["search_status"],
        "external_search_calls": report["external_search_calls"], "llm_calls": report["llm_calls"],
        "probabilities": [run["base_case_probability"], run["bull_case_probability"], run["bear_case_probability"]],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
