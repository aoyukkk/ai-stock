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

from database.models.entry_timing_v2 import AdmissionV2Run
from database.session import get_session, init_db
from quant.explainability.factor_performance import FactorPerformanceService
from quant.explainability.gate_evaluation_service import GateEvaluationService
from quant.explainability.service import DecisionExplainabilityShadowService
from scripts.migrate_admission_v3_explainability_enhancement import main as migrate_schema


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill and evaluate Admission V3 explainability in shadow mode.")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--confirm-shadow", action="store_true")
    args = parser.parse_args()
    if not args.confirm_shadow:
        raise ValueError("EXPLAINABILITY_EVALUATION_SHADOW_CONFIRMATION_REQUIRED")
    if args.start_date > args.end_date:
        raise ValueError("INVALID_EXPLAINABILITY_EVALUATION_PERIOD")
    migrate_schema()
    init_db()
    session = get_session()
    try:
        dates = list(session.scalars(
            select(AdmissionV2Run.trade_date)
            .where(
                AdmissionV2Run.trade_date >= args.start_date,
                AdmissionV2Run.trade_date <= args.end_date,
                AdmissionV2Run.status == "SUCCESS",
            )
            .distinct()
            .order_by(AdmissionV2Run.trade_date)
        ))
        runs = [
            DecisionExplainabilityShadowService(session).run(day, force_shadow=True)
            for day in dates
        ]
        factor_performance = FactorPerformanceService(session).evaluate(
            args.start_date,
            args.end_date,
            persist=True,
        )
        gate_evaluation = GateEvaluationService(session).evaluate(args.start_date, args.end_date)
        print(json.dumps({
            "period": f"{args.start_date}..{args.end_date}",
            "shadow_runs": runs,
            "factor_performance_history": factor_performance,
            "gate_evaluation": gate_evaluation,
            "production_promoted": False,
        }, ensure_ascii=False, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
