from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-ending", type=date.fromisoformat, required=True)
    parser.add_argument("--factor-version", required=True)
    parser.add_argument("--skip-excel", action="store_true")
    args = parser.parse_args()
    start = args.week_ending - timedelta(days=4)
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_full_universe_quant_validation.py"),
        "--start-date",
        start.isoformat(),
        "--end-date",
        args.week_ending.isoformat(),
        "--as-of-date",
        args.week_ending.isoformat(),
        "--factor-version",
        args.factor_version,
        "--no-network",
        "--skip-excel",
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
