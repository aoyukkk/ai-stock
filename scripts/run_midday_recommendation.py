from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db
from midday.service import MiddayRecommendationService


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the advisory-only midday recommendation pipeline")
    parser.add_argument("--trade-date", type=date.fromisoformat)
    parser.add_argument("--resume-llm", metavar="RUN_ID")
    parser.add_argument("--refresh-scoring", metavar="RUN_ID")
    parser.add_argument("--finalize-ranking", metavar="RUN_ID")
    parser.add_argument("--normalize-actions", metavar="RUN_ID")
    parser.add_argument("--retry-missing-llm", metavar="RUN_ID")
    parser.add_argument("--reconcile-usage", metavar="RUN_ID")
    parser.add_argument("--decision-time", type=datetime.fromisoformat)
    parser.add_argument("--historical-validation", action="store_true")
    parser.add_argument("--no-external", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    init_db()
    session = get_session()
    try:
        service = MiddayRecommendationService(session)
        if args.reconcile_usage:
            result = service.reconcile_usage(args.reconcile_usage)
        elif args.retry_missing_llm:
            result = service.retry_missing_llm(args.retry_missing_llm)
        elif args.normalize_actions:
            result = service.normalize_actions(args.normalize_actions)
        elif args.finalize_ranking:
            result = service.finalize_llm_ranking(args.finalize_ranking)
        elif args.refresh_scoring:
            result = service.refresh_persisted_scoring(args.refresh_scoring)
        elif args.resume_llm:
            result = service.resume_llm(args.resume_llm)
        else:
            if args.trade_date is None:
                parser.error("--trade-date is required unless --resume-llm is used")
            result = service.run(
                args.trade_date,
                decision_time=args.decision_time,
                historical_validation=args.historical_validation,
                allow_real_external=not args.no_external,
                force=args.force,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        status = result.get("status")
        return 0 if status is None or status in {"SUCCESS", "PARTIAL_SUCCESS", "WAITING_AFTERNOON_RECHECK", "MISSED_MIDDAY_WINDOW"} else 2
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
