from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from database.models.event_overlay import EventScreeningRunRecord  # noqa: E402
from database.models.ranking_evaluation import (  # noqa: E402
    ModelEffectivenessStageSnapshot,
    RankingEvaluationSnapshot,
)
from database.session import get_session, init_db  # noqa: E402
from services.ranking_evaluation.model_stage_service import ModelStageEffectivenessService  # noqa: E402
from services.ranking_evaluation.model_stage_weekly_service import ModelStageWeeklyService  # noqa: E402
from services.ranking_evaluation.outcome_backfill_service import OutcomeBackfillService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="One-shot model-stage effectiveness runner.")
    parser.add_argument("--mode", required=True, choices=("daily", "weekly"))
    parser.add_argument("--as-of-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--week-ending", type=date.fromisoformat)
    parser.add_argument("--quant-factor-version")
    parser.add_argument("--screening-version")
    parser.add_argument("--no-excel", action="store_true")
    args = parser.parse_args()
    if args.mode == "daily":
        init_db()
        session = get_session()
        try:
            service = ModelStageEffectivenessService(session)
            captures = []
            warnings = []
            cohort = session.scalar(
                select(RankingEvaluationSnapshot).where(
                    RankingEvaluationSnapshot.ranking_trade_date == args.as_of_date,
                    RankingEvaluationSnapshot.factor_version
                    == "TUSHARE_QUANT_V2_CORRECTED_SHADOW",
                )
            )
            if cohort:
                try:
                    captures.append(
                        service.capture_quant_cohort(
                            trade_date=args.as_of_date,
                            factor_version="TUSHARE_QUANT_V2_CORRECTED_SHADOW",
                        )
                    )
                except Exception as exc:
                    warnings.append(f"QUANT_CAPTURE:{type(exc).__name__}:{exc}")
                checkpoint = (
                    ROOT / "outputs" / "quant_v2_validation"
                    / args.as_of_date.isoformat() / ".monday_v2_llm_checkpoint.json"
                )
                if checkpoint.exists():
                    try:
                        captures.append(
                            service.capture_v2_checkpoint(
                                trade_date=args.as_of_date,
                                checkpoint_path=checkpoint,
                                audit_path=checkpoint.parent / "monday_v2_candidate_audit.json",
                            )
                        )
                    except Exception as exc:
                        warnings.append(f"FLASH_V2_CAPTURE:{type(exc).__name__}:{exc}")
                event_run = session.scalar(
                    select(EventScreeningRunRecord)
                    .where(EventScreeningRunRecord.trade_date == args.as_of_date)
                    .order_by(EventScreeningRunRecord.id.desc())
                )
                if event_run:
                    try:
                        captures.append(
                            service.capture_v3_run(screening_run_id=event_run.run_id)
                        )
                    except Exception as exc:
                        warnings.append(f"FLASH_V3_CAPTURE:{type(exc).__name__}:{exc}")
            outcome = OutcomeBackfillService(session).refresh(as_of_date=args.as_of_date)
            stages = list(session.scalars(select(ModelEffectivenessStageSnapshot)))
            metric_count = 0
            for stage in stages:
                for horizon in (1, 3, 5, 10):
                    metric_count += len(
                        service.calculate_daily_metrics(stage.id, horizon=horizon)
                    )
            result = {
                "status": "COMPLETED_WITH_WARNINGS" if warnings else "COMPLETED",
                "as_of_date": args.as_of_date,
                "captures": captures,
                "warnings": warnings,
                "outcome_reuse": outcome,
                "daily_metrics_refreshed": metric_count,
                "shadow_only": True,
                "llm_calls": 0,
                "external_search_calls": 0,
                "orders": 0,
                "scheduler": False,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0
        finally:
            session.close()
    if not args.quant_factor_version or not args.screening_version:
        parser.error("weekly mode requires --quant-factor-version and --screening-version")
    week_ending = args.week_ending or (
        args.as_of_date - timedelta(days=(args.as_of_date.weekday() - 4) % 7)
    )
    init_db()
    session = get_session()
    try:
        result = ModelStageWeeklyService(session).run(
            week_ending=week_ending,
            quant_factor_version=args.quant_factor_version,
            screening_version=args.screening_version,
            output_excel=not args.no_excel,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
