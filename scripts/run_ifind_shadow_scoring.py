from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models import QuantRankResult
from database.session import get_session, init_db
from post_close.service import IFindEnhancementService


def main() -> int:
    parser = argparse.ArgumentParser(description="Run advisory-only iFinD Shadow scoring over an existing Quant run")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--quant-run-id")
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.trade_date)
    init_db(); session = get_session()
    try:
        before_rows = list(session.scalars(select(QuantRankResult).order_by(QuantRankResult.id)))
        before = _digest(before_rows)
        result = IFindEnhancementService(session).run_shadow(trade_date, quant_run_id=args.quant_run_id)
        after_rows = list(session.scalars(select(QuantRankResult).order_by(QuantRankResult.id)))
        output = {
            "status": result["status"], "run_id": result["run_id"],
            "baseline_before_count": len(before_rows), "baseline_after_count": len(after_rows),
            "baseline_hash_unchanged": before == _digest(after_rows), "summary": result,
            "llm_calls": 0, "official_ranking_changed": False,
        }
        print(json.dumps(output, ensure_ascii=True, default=str))
        return 0 if output["baseline_hash_unchanged"] else 2
    finally:
        session.close()


def _digest(rows) -> str:
    payload = [(row.id, row.quant_run_id, row.stock_code, row.rank, str(row.total_score), str(row.technical_score), str(row.capital_score), str(row.emotion_score), str(row.momentum_score), str(row.risk_score)) for row in rows]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
