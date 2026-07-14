from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db
from fundamentals.external_evidence import (
    VerifiedFundamentalCompletionInput,
    VerifiedFundamentalCompletionService,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply an audited external-evidence fundamental completion."
    )
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    payload = VerifiedFundamentalCompletionInput.model_validate_json(
        args.input.read_text(encoding="utf-8")
    )
    init_db()
    with get_session() as session:
        result = VerifiedFundamentalCompletionService(session).apply(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
