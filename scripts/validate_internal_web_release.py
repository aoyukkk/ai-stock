from __future__ import annotations

"""Fail-closed validation for the deployable internal-Web release directory.

Reports only paths and finding classes: it never prints file contents or
credential values.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path


TOOL_VERSION = "1.0"
FORBIDDEN_NAMES = (".env", "ifind_probe.yaml")
FORBIDDEN_PATH_WORDS = ("credential", "tunnel-token", "session-secret")
FORBIDDEN_CONFIG_WORDS = ("ifind", "tushare", "llm", "secret", "token")
GENERIC_SECRET = re.compile(rb"(?i)(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|cf-[A-Za-z0-9_-]{20,})")
LITERAL_SECRET_FIELD = re.compile(
    rb"(?im)^\s*(?:password|passwd|pwd|token|api[_-]?key|secret|authorization)\s*[:=]\s*['\"]?(?!\$\{?[A-Za-z_][A-Za-z0-9_]*\}?\s*$|CHANGE_ME\s*$|example_password\s*$)[^\s#'\"]+"
)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def validate(root: Path) -> dict[str, object]:
    manifest_path = root / "release-manifest.json"
    findings: list[dict[str, str]] = []
    if not manifest_path.is_file():
        return {"passed": False, "tool_version": TOOL_VERSION, "findings": [{"path": "release-manifest.json", "type": "manifest_missing"}]}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"passed": False, "tool_version": TOOL_VERSION, "findings": [{"path": "release-manifest.json", "type": "manifest_invalid"}]}
    hashes = manifest.get("file_hashes") if isinstance(manifest.get("file_hashes"), dict) else {}
    for relative, expected in hashes.items():
        path = root / relative
        if not path.is_file() or digest(path) != str(expected).lower():
            findings.append({"path": str(relative), "type": "hash_mismatch"})
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        lowered = relative.lower()
        name = path.name.lower()
        if name in FORBIDDEN_NAMES or name.endswith((".db", ".sqlite", ".sqlite3")):
            findings.append({"path": relative, "type": "forbidden_artifact"})
            continue
        if any(word in lowered for word in FORBIDDEN_PATH_WORDS):
            findings.append({"path": relative, "type": "forbidden_credential_path"})
            continue
        if relative.startswith("config/") and any(word in lowered for word in FORBIDDEN_CONFIG_WORDS):
            findings.append({"path": relative, "type": "provider_config_not_allowed"})
            continue
        try:
            payload = path.read_bytes()
        except OSError:
            findings.append({"path": relative, "type": "unreadable"})
            continue
        is_configuration = path.suffix.lower() in {".env", ".yaml", ".yml", ".json", ".toml", ".ini"}
        if GENERIC_SECRET.search(payload) or (is_configuration and LITERAL_SECRET_FIELD.search(payload)):
            findings.append({"path": relative, "type": "secret_pattern"})
    required = {"migrations/20260722_internal_auth_hotfix.sql", "scripts/fix_internal_auth_state.py", "scripts/set_internal_shared_password.py", "ROLLBACK.md"}
    for path in sorted(required):
        if not (root / path).is_file():
            findings.append({"path": path, "type": "required_artifact_missing"})
    if manifest.get("auth_mode") != "LOCAL_SHARED_PASSWORD":
        findings.append({"path": "release-manifest.json", "type": "invalid_auth_mode"})
    return {"passed": not findings, "tool_version": TOOL_VERSION, "files_scanned": sum(1 for p in root.rglob("*") if p.is_file()), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    report = validate(args.root.resolve())
    print(json.dumps({"passed": report["passed"], "tool_version": TOOL_VERSION, "files_scanned": report.get("files_scanned", 0), "finding_count": len(report["findings"])}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
