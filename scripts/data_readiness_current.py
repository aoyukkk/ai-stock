from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path: sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env", override=False)

from temporal.readiness import DataReadinessService
from temporal.schemas import RunMode


if __name__ == "__main__":
    service = DataReadinessService()
    context, manifest = service.check(RunMode.POST_MARKET_FINAL)
    print(json.dumps(service.payload(context, manifest), ensure_ascii=False, indent=2, default=str))
