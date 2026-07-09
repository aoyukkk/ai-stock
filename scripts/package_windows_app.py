from __future__ import annotations

import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts import check_security_config
from scripts._common import FRONTEND_DIR, ROOT_DIR, npm_command, npx_command, run_command


def main() -> int:
    security_report = check_security_config.run_checks(ROOT_DIR)
    if not security_report.ok:
        check_security_config.print_report(security_report)
        return 1

    npm = npm_command()
    if not npm:
        print("npm is not installed. Install Node.js before packaging the Windows app.")
        return 0

    code = run_command([npm, "run", "build"], cwd=FRONTEND_DIR)
    if code != 0:
        return code

    npx = npx_command()
    if not npx:
        print("npx is not installed. Electron Builder packaging skipped.")
        return 0

    return run_command(
        [npx, "electron-builder", "--config", "electron-builder.config.js", "--win", "--publish", "never"],
        cwd=FRONTEND_DIR,
    )


if __name__ == "__main__":
    raise SystemExit(main())
