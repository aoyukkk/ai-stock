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
from services.ranking_evaluation.model_stage_service import (  # noqa: E402
    ModelStageEffectivenessService,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture immutable Quant/Flash effectiveness stage.")
    parser.add_argument("--trade-date", required=True, type=date.fromisoformat)
    parser.add_argument("--quant-run-id", required=True)
    parser.add_argument("--screening-run-id", required=True)
    parser.add_argument("--screening-version", required=True)
    parser.add_argument("--checkpoint-path", type=Path)
    parser.add_argument("--audit-path", type=Path)
    args = parser.parse_args()
    init_db()
    session = get_session()
    try:
        service = ModelStageEffectivenessService(session)
        if args.screening_version.upper().startswith("QUANT_STAGE"):
            result = service.capture_quant_cohort(
                trade_date=args.trade_date,
                factor_version="TUSHARE_QUANT_V2_CORRECTED_SHADOW",
            )
        elif "EVENT_OVERLAY" in args.screening_version.upper():
            result = service.capture_v3_run(screening_run_id=args.screening_run_id)
        else:
            checkpoint = args.checkpoint_path or (
                ROOT / "outputs" / "quant_v2_validation" / args.trade_date.isoformat()
                / ".monday_v2_llm_checkpoint.json"
            )
            audit = args.audit_path or checkpoint.parent / "monday_v2_candidate_audit.json"
            result = service.capture_v2_checkpoint(
                trade_date=args.trade_date,
                checkpoint_path=checkpoint,
                audit_path=audit,
            )
        if result.get("screening_run_id") != args.screening_run_id:
            raise ValueError("SCREENING_RUN_ID_MISMATCH")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
