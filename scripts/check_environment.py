from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts._common import (
    FRONTEND_DIR,
    ROOT_DIR,
    CheckReport,
    load_yaml,
    npm_command,
    print_report,
)


REQUIRED_CONFIG_FILES = (
    "system.yaml",
    "stock_scan.yaml",
    "schedule.yaml",
    "market_data.yaml",
    "event_trigger.yaml",
    "models.yaml",
    "agents.yaml",
    "ai_score.yaml",
    "llm.yaml",
    "token_cost.yaml",
    "risk_rules.yaml",
    "risk.yaml",
    "order_price.yaml",
    "memory.yaml",
    "paper_trading.yaml",
    "virtual_trading.yaml",
    "ui.yaml",
    "data_sources.yaml",
    "frontend.yaml",
)


def run_checks(root: Path = ROOT_DIR) -> CheckReport:
    report = CheckReport()

    if sys.version_info >= (3, 11):
        report.add_info(f"Python {sys.version.split()[0]} >= 3.11")
    else:
        report.add_error("Python >= 3.11 is required")

    for required in ("requirements.txt", ".env.example"):
        path = root / required
        if path.exists():
            report.add_info(f"{required} exists")
        else:
            report.add_error(f"{required} is missing")

    package_json = root / "frontend" / "package.json"
    if package_json.exists():
        report.add_info("frontend/package.json exists")
    else:
        report.add_error("frontend/package.json is missing")

    for filename in REQUIRED_CONFIG_FILES:
        path = root / "config" / filename
        if not path.exists():
            report.add_error(f"config/{filename} is missing")
            continue
        try:
            load_yaml(path)
            report.add_info(f"config/{filename} parses")
        except Exception as exc:
            report.add_error(f"config/{filename} failed to parse: {exc}")

    try:
        module = importlib.import_module("backend.main")
        getattr(module, "app")
        report.add_info("backend.main:app imports")
    except Exception as exc:
        report.add_error(f"backend.main:app import failed: {exc}")

    try:
        import database.models  # noqa: F401
        from database.base import Base
        from database.session import create_engine_from_url

        engine = create_engine_from_url("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        engine.dispose()
        report.add_info("SQLite database can be initialized")
    except Exception as exc:
        report.add_error(f"SQLite initialization failed: {exc}")

    npm = npm_command()
    if npm:
        report.add_info(f"npm found: {npm}")
        try:
            result = subprocess.run(
                [npm, "--version"],
                cwd=str(FRONTEND_DIR),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                report.add_info(f"npm version {result.stdout.strip()}")
        except Exception as exc:
            report.add_warning(f"npm version check skipped: {exc}")
    else:
        report.add_warning("npm is not available; install Node.js before frontend packaging")

    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            report.add_info(f"Node {result.stdout.strip()}")
        else:
            report.add_warning("node is not available")
    except FileNotFoundError:
        report.add_warning("node is not available")

    return report


def main() -> int:
    report = run_checks()
    print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
