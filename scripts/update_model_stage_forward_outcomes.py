from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.ranking_evaluation import ModelEffectivenessStageSnapshot  # noqa: E402
from database.session import get_session, init_db  # noqa: E402
from services.ranking_evaluation.model_stage_service import ModelStageEffectivenessService  # noqa: E402
from services.ranking_evaluation.outcome_backfill_service import OutcomeBackfillService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Reuse ranking outcomes and refresh model-stage metrics.")
    parser.add_argument("--as-of-date", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    init_db()
    session = get_session()
    try:
        outcome = OutcomeBackfillService(session).refresh(as_of_date=args.as_of_date)
        stages = list(session.scalars(select(ModelEffectivenessStageSnapshot)))
        metric_count = 0
        service = ModelStageEffectivenessService(session)
        for stage in stages:
            for horizon in (1, 3, 5, 10):
                metric_count += len(service.calculate_daily_metrics(stage.id, horizon=horizon))
        result = {
            "status": "COMPLETED",
            "as_of_date": args.as_of_date,
            "outcome_reuse": outcome,
            "stage_snapshot_count": len(stages),
            "daily_metrics_refreshed": metric_count,
            "llm_calls": 0,
            "external_search_calls": 0,
            "orders": 0,
            "scheduler": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
