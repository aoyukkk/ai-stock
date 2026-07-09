from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts._common import FRONTEND_DIR, ROOT_DIR, npm_command


def preflight(root: Path = ROOT_DIR) -> bool:
    package_json = root / "frontend" / "package.json"
    if not package_json.exists():
        print("frontend/package.json not found.")
        return False

    if not (root / "frontend" / "node_modules").exists():
        print("frontend/node_modules not found. Run: cd frontend && npm install")

    if not npm_command():
        print("npm not found. Install Node.js before starting the frontend.")
        return False

    return True


def main() -> int:
    if not preflight():
        return 1

    npm = npm_command()
    assert npm is not None
    print("Starting frontend: http://127.0.0.1:5173")
    print("Mode: local control panel, real trading disabled")
    return subprocess.call([npm, "run", "dev"], cwd=str(FRONTEND_DIR))


if __name__ == "__main__":
    raise SystemExit(main())
