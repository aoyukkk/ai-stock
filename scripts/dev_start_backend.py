from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts._common import ROOT_DIR, env_value, is_false


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "8000"


def preflight(root: Path = ROOT_DIR) -> bool:
    if sys.version_info < (3, 11):
        print("Python >= 3.11 is required.")
        return False

    if not (root / ".env").exists():
        print(".env not found. Run: python scripts/init_local_env.py")
        return False

    if not is_false(env_value(root, "ENABLE_REAL_TRADING")):
        print("ENABLE_REAL_TRADING must be false before backend startup.")
        return False

    return True


def main() -> int:
    if not preflight():
        return 1

    host = os.getenv("BACKEND_HOST", DEFAULT_HOST)
    port = os.getenv("BACKEND_PORT", DEFAULT_PORT)
    if host not in {"127.0.0.1", "localhost"}:
        print("Backend host must remain local-only in Phase 15.")
        return 1

    print(f"Starting backend: http://{host}:{port}")
    print("Mode: real_trading_enabled=false, data_source=mock, llm=mock")
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            host,
            "--port",
            port,
        ],
        cwd=str(ROOT_DIR),
    )


if __name__ == "__main__":
    raise SystemExit(main())
