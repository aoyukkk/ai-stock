"""Fail-closed workspace scan for values loaded from the project .env file.

The scanner never emits the values, fragments, lengths, or hashes of secrets.
Exit codes: 0 clean, 1 exposure detected, 2 scan incomplete, 3 both conditions.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "outputs" / "security_remediation_20260730" / "workspace_secret_scan.json"
SCAN_ROOTS = (
    "backend", "config", "configs", "datasource", "docs", "frontend", "llm_gateway",
    "logs", "outputs", "reports", "release", "build", "temp", "tmp", "backups",
    "scripts", "tests", "trading",
)
EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
MAX_FILE_BYTES = 20 * 1024 * 1024


def load_secret_values(env_file: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    if not env_file.exists():
        return result
    for line in env_file.read_text(encoding="utf-8-sig", errors="strict").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, raw = stripped.removeprefix("export ").split("=", 1)
        name, raw = name.strip(), raw.strip()
        if not name or not any(marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")):
            continue
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        if raw and len(raw) >= 4 and not raw.startswith("${"):
            result[name] = raw.encode("utf-8")
    return result


def candidate_files(permission_denied: list[dict[str, str]]) -> Iterable[Path]:
    def onerror(error: OSError) -> None:
        permission_denied.append({"path": relative(Path(error.filename or ".")), "operation": "traverse"})

    for root_name in SCAN_ROOTS:
        root = ROOT / root_name
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for directory, dirnames, filenames in os.walk(root, onerror=onerror):
            dirnames[:] = [name for name in dirnames if name not in EXCLUDED_DIRS]
            for filename in filenames:
                path = Path(directory) / filename
                if path.resolve() == (ROOT / ".env").resolve():
                    continue
                yield path


def scan(output: Path) -> tuple[dict, int]:
    secrets = load_secret_values(ROOT / ".env")
    findings: list[dict[str, object]] = []
    denied: list[dict[str, str]] = []
    skipped: list[dict[str, object]] = []
    scanned = 0
    output_resolved = output.resolve()
    for path in candidate_files(denied):
        try:
            if path.resolve() == output_resolved:
                continue
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                skipped.append({"path": relative(path), "reason": "FILE_TOO_LARGE", "bytes": size})
                continue
            content = path.read_bytes()
        except PermissionError:
            denied.append({"path": relative(path), "operation": "read"})
            continue
        except OSError as exc:
            denied.append({"path": relative(path), "operation": f"read:{type(exc).__name__}"})
            continue
        scanned += 1
        is_binary = b"\0" in content[:8192]
        for variable, value in secrets.items():
            if value not in content:
                continue
            if is_binary:
                findings.append({
                    "variable": variable, "file": relative(path), "binary_match": True, "severity": "CRITICAL"
                })
                continue
            for line_number, line in enumerate(content.splitlines(), 1):
                if value in line:
                    findings.append({
                        "variable": variable,
                        "file": relative(path),
                        "line": line_number,
                        "binary_match": False,
                        "severity": "CRITICAL",
                    })
    status = "EXPOSURE_DETECTED" if findings else "INCOMPLETE" if denied else "CLEAN"
    if findings and denied:
        status = "EXPOSURE_DETECTED_AND_INCOMPLETE"
    report = {
        "schema_version": 1,
        "status": status,
        "secret_variables_loaded": sorted(secrets),
        "secret_values_printed": False,
        "files_scanned": scanned,
        "findings": findings,
        "permission_denied": denied,
        "skipped": skipped,
        "excluded": [".env", ".git/**", "node_modules/**", "large files over 20 MiB"],
    }
    code = (1 if findings else 0) | (2 if denied else 0)
    return report, code


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report, code = scan(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "files_scanned": report["files_scanned"],
        "findings": len(report["findings"]),
        "permission_denied": len(report["permission_denied"]),
        "report": relative(args.output),
    }, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
