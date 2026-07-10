from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env", override=False)

from fundamentals.tushare_service import TushareFundamentalBatchService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", action="append", dest="periods")
    parser.add_argument("--interfaces", default="income,balancesheet,cashflow,fina_indicator,mainbz")
    parser.add_argument("--mainbz-types", default="P,I,D")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Perform Provider calls; default is dry-run.")
    args = parser.parse_args()
    periods = args.periods or ["20250331"]
    result = TushareFundamentalBatchService().prewarm(
        periods,
        [item for item in args.interfaces.split(",") if item],
        mainbz_types=[item for item in args.mainbz_types.split(",") if item],
        force=args.force,
        dry_run=not args.execute,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
