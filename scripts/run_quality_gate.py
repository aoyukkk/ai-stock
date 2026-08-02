"""Repeatable local security and quality gate; no provider, LLM, scheduler, or release publishing."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "security_remediation_20260730"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Skip full pytest and desktop packaging, but keep security checks.")
    parser.add_argument("--continue-on-failure", action="store_true", help="Collect all results instead of stopping at first failure.")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "ENABLE_REAL_TRADING": "false",
        "SCHEDULER_ENABLED": "false",
        "PAPER_TRADING_ENABLED": "false",
        "AUTO_ORDER_CREATION": "false",
        "LLM_REAL_CALLS_ENABLED": "false",
        "RUN_REAL_FUNDAMENTAL_RESEARCH": "false",
        "AI_TRADER_DISABLE_EXTERNAL_PROVIDERS": "true",
        "CSC_IDENTITY_AUTO_DISCOVERY": "false",
    })
    py = sys.executable
    frontend = ROOT / "frontend"
    stages: list[tuple[str, list[str], Path]] = [
        ("security_config", [py, "scripts/check_security_config.py"], ROOT),
        ("workspace_secret_scan", [py, "scripts/check_workspace_secret_exposure.py"], ROOT),
        ("pip_check", [py, "-m", "pip", "check"], ROOT),
        ("pip_audit", [py, "-m", "pip_audit", "--format", "json", "--output", str(OUTPUT / "pip_audit.json")], ROOT),
        ("bandit_high", [py, "-m", "bandit", "-lll", "-r", "backend", "datasource", "llm_gateway", "trading", "scripts", "-f", "json", "-o", str(OUTPUT / "bandit_gate.json")], ROOT),
        ("ruff", [py, "-m", "ruff", "check", "backend", "datasource", "llm_gateway", "trading", "scripts"], ROOT),
        ("python_security_tests", [py, "-m", "pytest", "tests/test_security_remediation.py", "-q"], ROOT),
    ]
    if not args.quick:
        stages.append(("python_tests", [py, "-m", "pytest"], ROOT))
    stages.extend([
        ("frontend_tests", ["npm.cmd", "test"], frontend),
        ("typecheck", ["npm.cmd", "run", "typecheck"], frontend),
        ("npm_audit_production", ["npm.cmd", "audit", "--omit=dev", "--json"], frontend),
        ("npm_audit_full", ["npm.cmd", "audit", "--json"], frontend),
        ("frontend_build", ["npm.cmd", "run", "build"], frontend),
    ])
    if not args.quick:
        stages.append((
            "electron_package",
            ["npx.cmd", "electron-builder", "--config", "electron-builder.review.config.js", "--win", "--dir",
             "--publish", "never"],
            frontend,
        ))
    stages.extend([
        ("internal_web_release", [py, "scripts/validate_internal_web_release.py", "release/internal-web"], ROOT),
        ("git_diff_check", ["git", "diff", "--check"], ROOT),
    ])

    results = []
    failed = False
    for name, command, cwd in stages:
        started = datetime.now(timezone.utc)
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        record = {
            "name": name,
            "returncode": completed.returncode,
            "passed": completed.returncode == 0,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "command": redact_command(command),
            "stdout_tail": (completed.stdout or "")[-4000:],
            "stderr_tail": (completed.stderr or "")[-4000:],
        }
        results.append(record)
        print(f"{name}: {'PASS' if record['passed'] else 'FAIL'}")
        failed = failed or not record["passed"]
        if failed and not args.continue_on_failure:
            break
    report = {
        "schema_version": 1,
        "status": "PASS" if not failed else "FAIL",
        "safety": {
            "real_trading": False,
            "scheduler": False,
            "orders": False,
            "real_llm": False,
            "external_providers": False,
            "release_published": False,
        },
        "results": results,
    }
    (OUTPUT / "quality_gate_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if failed else 0


def redact_command(command: list[str]) -> list[str]:
    return ["[REDACTED]" if any(marker in item.upper() for marker in ("TOKEN=", "PASSWORD=", "SECRET=")) else item for item in command]


if __name__ == "__main__":
    raise SystemExit(main())
