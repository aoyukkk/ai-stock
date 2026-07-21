from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db
from post_close.service import PostCloseActionService


def main() -> int:
    parser = argparse.ArgumentParser(description="Run advisory-only post-close action rules")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--mode", choices=("POST_CLOSE_FAST", "POST_CLOSE_FINAL"), default="POST_CLOSE_FAST")
    parser.add_argument("--allow-historical", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    init_db(); session = get_session()
    try:
        result = PostCloseActionService(session).run_fast(date.fromisoformat(args.trade_date), run_mode=args.mode, allow_historical=args.allow_historical, force=args.force)
        print(json.dumps(result, ensure_ascii=True, default=str))
        return 0 if result.get("status") in {"SUCCESS", "WAITING_FOR_POST_CLOSE_RUN", "WAITING_FOR_FINAL_DATA"} else 2
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
