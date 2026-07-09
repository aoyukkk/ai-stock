from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts._common import ROOT_DIR, env_value, is_false


def main() -> int:
    if not is_false(env_value(ROOT_DIR, "ENABLE_REAL_TRADING")):
        print("Production local startup is blocked because ENABLE_REAL_TRADING is not false.")
        return 1

    print("Production local startup skeleton")
    print("Backend URL: http://127.0.0.1:8000")
    print("Frontend static assets should be served by Electron or a local preview server.")
    print("Mode: Mock Provider + Mock LLM + AI Simulation")
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=str(ROOT_DIR),
    )


if __name__ == "__main__":
    raise SystemExit(main())
