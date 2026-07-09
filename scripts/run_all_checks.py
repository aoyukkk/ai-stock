from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts._common import FRONTEND_DIR, ROOT_DIR, npm_command


def main() -> int:
    checks = [
        ("environment", [sys.executable, "scripts/check_environment.py"], ROOT_DIR),
        ("security", [sys.executable, "scripts/check_security_config.py"], ROOT_DIR),
        ("final smoke", [sys.executable, "scripts/run_final_smoke.py"], ROOT_DIR),
        ("pytest", [sys.executable, "-m", "pytest"], ROOT_DIR),
        ("compileall", [sys.executable, "-m", "compileall", "."], ROOT_DIR),
    ]

    npm = npm_command()
    if npm and (FRONTEND_DIR / "node_modules").exists():
        checks.extend(
            [
                ("frontend typecheck", [npm, "run", "typecheck"], FRONTEND_DIR),
                ("frontend build", [npm, "run", "build"], FRONTEND_DIR),
            ]
        )
    elif npm:
        print("frontend/node_modules is missing. Run: cd frontend && npm install")
        return 1
    else:
        print("npm is missing. Install Node.js before final frontend checks.")
        return 1

    summary: list[tuple[str, int]] = []
    for name, command, cwd in checks:
        print(f"\n=== {name} ===")
        print(f"$ {' '.join(command)}")
        completed = subprocess.run(command, cwd=str(cwd), check=False)
        summary.append((name, completed.returncode))
        if completed.returncode != 0:
            break

    print("\nFinal check summary:")
    for name, code in summary:
        print(f"- {name}: {'PASS' if code == 0 else f'FAIL ({code})'}")

    return 0 if all(code == 0 for _, code in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
