from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from backend.core.config import get_app_config
from llm_gateway.connectivity import run_connectivity_test
from llm_gateway.service import LLMGatewayService


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    if not _enabled(os.environ.get("RUN_REAL_LLM_SMOKE")):
        print(json.dumps({"executed": False, "reason": "RUN_REAL_LLM_SMOKE is not enabled."}))
        return 0

    provider_config = (
        get_app_config().config_files.get("models", {})
        .get("llm", {})
        .get("providers", {})
        .get("deepseek", {})
    )
    key_env = str(provider_config.get("api_key_env") or "DEEPSEEK_API_KEY")
    if not os.environ.get(key_env, "").strip():
        print(json.dumps({"executed": False, "provider": "deepseek", "status": "NOT_CONFIGURED"}))
        return 0

    result = run_connectivity_test(LLMGatewayService(), "light-screening-default")
    output = {"executed": True, **result}
    print(json.dumps(output, ensure_ascii=False))
    return 0 if result["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
