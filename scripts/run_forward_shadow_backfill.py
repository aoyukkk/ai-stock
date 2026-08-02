from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session
from review.forward_shadow import BackfillOptions, ForwardShadowBackfillService
from review.forward_shadow_report import ForwardShadowWorkbookExporter
from scripts.migrate_forward_shadow_evaluation_v1 import main as migrate


def _bool(value: str) -> bool:
    if value.lower() not in {"true", "false"}:
        raise argparse.ArgumentTypeError("expected true or false")
    return value.lower() == "true"


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline forward Shadow outcome backfill")
    parser.add_argument("--as-of-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--source-run-id")
    parser.add_argument("--matured-only", type=_bool, default=True)
    parser.add_argument("--execution-policy", default="NEXT_OPEN", choices=["NEXT_OPEN"])
    parser.add_argument("--no-external-api", type=_bool, default=True)
    parser.add_argument("--no-llm", type=_bool, default=True)
    parser.add_argument("--no-orders", type=_bool, default=True)
    args = parser.parse_args()
    migrate()
    session = get_session()
    try:
        result = ForwardShadowBackfillService(session).run(BackfillOptions(
            as_of_date=args.as_of_date, source_run_id=args.source_run_id, matured_only=args.matured_only,
            execution_policy=args.execution_policy, no_external_api=args.no_external_api,
            no_llm=args.no_llm, no_orders=args.no_orders))
        excel, markdown = ForwardShadowWorkbookExporter(session).export(args.as_of_date)
        print(json.dumps({**result, "excel": str(excel), "markdown": str(markdown)}, ensure_ascii=False, indent=2, default=str))
    finally:
        session.close()


if __name__ == "__main__":
    main()
