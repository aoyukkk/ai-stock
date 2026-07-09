from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts import check_security_config
from scripts._common import ROOT_DIR, run_command


def main() -> int:
    security_report = check_security_config.run_checks(ROOT_DIR)
    if not security_report.ok:
        check_security_config.print_report(security_report)
        return 1

    for command in (
        [sys.executable, "-m", "pytest"],
        [sys.executable, "-m", "compileall", "."],
    ):
        code = run_command(command, cwd=ROOT_DIR)
        if code != 0:
            return code

    pyinstaller = shutil.which("pyinstaller")
    if not pyinstaller:
        print("PyInstaller is not installed. Install with: pip install pyinstaller")
        print("Security checks and validation completed; packaging command skipped.")
        return 0

    return run_command([pyinstaller, "packaging/pyinstaller_backend.spec"], cwd=ROOT_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
