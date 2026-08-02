"""Sensitive-content and forbidden-artifact scan for an unpacked desktop review build."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_workspace_secret_exposure import load_secret_values


DEFAULT_BUILD = ROOT / "outputs" / "security_remediation_20260730" / "desktop_build" / "win-unpacked"
DEFAULT_OUTPUT = ROOT / "outputs" / "security_remediation_20260730" / "desktop_build_sensitive_scan.json"
FORBIDDEN_NAMES = {".env", ".env.local", ".env.production"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
FORBIDDEN_TEXT = {
    b"http://127.0.0.1:5173": "DEVELOPMENT_RENDERER_URL",
    b"runtime:connection": "DEPRECATED_TOKEN_CONNECTION_IPC",
    b"getConnection": "DEPRECATED_RENDERER_CONNECTION_API",
}


def scan(build_root: Path) -> dict:
    secrets = load_secret_values(ROOT / ".env")
    forbidden_files: list[str] = []
    text_hits: list[dict[str, str]] = []
    secret_hits: list[dict[str, str]] = []
    files_scanned = 0
    for path in build_root.rglob("*"):
        if not path.is_file():
            continue
        files_scanned += 1
        relative = path.relative_to(build_root).as_posix()
        if path.name.lower() in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            forbidden_files.append(relative)
        try:
            content = path.read_bytes()
        except OSError:
            text_hits.append({"file": relative, "rule": "UNREADABLE_BUILD_FILE"})
            continue
        for needle, rule in FORBIDDEN_TEXT.items():
            if needle in content:
                if rule == "DEVELOPMENT_RENDERER_URL" or relative.lower().endswith("resources/app.asar"):
                    text_hits.append({"file": relative, "rule": rule})
        for variable, value in secrets.items():
            if value in content:
                secret_hits.append({"file": relative, "variable": variable})
    passed = not forbidden_files and not text_hits and not secret_hits
    return {
        "schema_version": 1,
        "passed": passed,
        "build_root": build_root.relative_to(ROOT).as_posix(),
        "files_scanned": files_scanned,
        "forbidden_files": forbidden_files,
        "forbidden_text_hits": text_hits,
        "known_secret_hits": secret_hits,
        "secret_values_printed": False,
        "checks": {
            "env_files": not any(Path(item).name.lower() in FORBIDDEN_NAMES for item in forbidden_files),
            "databases": not any(Path(item).suffix.lower() in FORBIDDEN_SUFFIXES for item in forbidden_files),
            "development_server_url": not any(item["rule"] == "DEVELOPMENT_RENDERER_URL" for item in text_hits),
            "deprecated_token_bridge": not any("TOKEN" in item["rule"] or "CONNECTION" in item["rule"] for item in text_hits),
            "known_workspace_secrets": not secret_hits,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_root", nargs="?", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = scan(args.build_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"],
        "files_scanned": report["files_scanned"],
        "forbidden_files": len(report["forbidden_files"]),
        "forbidden_text_hits": len(report["forbidden_text_hits"]),
        "known_secret_hits": len(report["known_secret_hits"]),
    }))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
