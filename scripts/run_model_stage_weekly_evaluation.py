from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db  # noqa: E402
from services.ranking_evaluation.model_stage_weekly_service import ModelStageWeeklyService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build immutable model-stage weekly evaluation.")
    parser.add_argument("--week-ending", required=True, type=date.fromisoformat)
    parser.add_argument("--quant-factor-version", required=True)
    parser.add_argument("--screening-version", required=True)
    parser.add_argument("--no-excel", action="store_true")
    args = parser.parse_args()
    init_db()
    session = get_session()
    try:
        result = ModelStageWeeklyService(session).run(
            week_ending=args.week_ending,
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
