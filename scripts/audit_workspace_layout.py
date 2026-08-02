"""Read-only workspace layout inventory. This script never deletes or moves files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "outputs" / "security_remediation_20260730" / "workspace_layout_audit.json"
SENSITIVE_NAME = re.compile(r"(\.env|\.db$|\.sqlite|secret|credential|token|password)", re.I)
DATE_PINNED = re.compile(r"(20\d{2}[-_]?\d{2}[-_]?\d{2})")
REBUILDABLE = {"dist", "dist-electron", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "build"}


def audit() -> dict:
    denied: list[dict[str, str]] = []
    directories: list[dict[str, object]] = []
    sensitive: list[str] = []
    date_pinned: list[str] = []
    rebuildable: list[str] = []
    for entry in sorted(ROOT.iterdir(), key=lambda item: item.name.lower()):
        if entry.name == ".git":
            continue
        files = 0
        total = 0

        def onerror(error: OSError) -> None:
            denied.append({"path": safe_relative(Path(error.filename or entry)), "operation": "traverse"})

        iterator = [(str(entry.parent), [], [entry.name])] if entry.is_file() else os.walk(entry, onerror=onerror)
        for directory, dirnames, filenames in iterator:
            dirnames[:] = [name for name in dirnames if name != ".git"]
            for name in filenames:
                path = Path(directory) / name
                try:
                    size = path.stat().st_size
                except OSError as exc:
                    denied.append({"path": safe_relative(path), "operation": f"stat:{type(exc).__name__}"})
                    continue
                files += 1
                total += size
                rel = safe_relative(path)
                if SENSITIVE_NAME.search(name):
                    sensitive.append(rel)
                if path.suffix.lower() in {".py", ".ps1", ".bat", ".cmd"} and DATE_PINNED.search(name):
                    date_pinned.append(rel)
                for index, part in enumerate(path.parts):
                    if part in REBUILDABLE:
                        rebuildable.append(Path(*path.parts[: index + 1]).name if index == 0 else safe_relative(Path(*path.parts[: index + 1])))
                        break
        directories.append({"path": entry.name, "file_count": files, "bytes": total})
    untracked_large = []
    result = subprocess.run(
        ["git", "status", "--porcelain", "-z"], cwd=ROOT, capture_output=True, check=False
    )
    for raw in result.stdout.decode("utf-8", errors="replace").split("\0"):
        if not raw.startswith("?? "):
            continue
        path = ROOT / raw[3:]
        if path.is_file():
            try:
                if path.stat().st_size >= 10 * 1024 * 1024:
                    untracked_large.append({"path": safe_relative(path), "bytes": path.stat().st_size})
            except OSError:
                denied.append({"path": safe_relative(path), "operation": "stat"})
    git_objects = subprocess.run(
        ["git", "count-objects", "-vH"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return {
        "schema_version": 1,
        "read_only": True,
        "status": "INCOMPLETE_PERMISSION_DENIED" if denied else "COMPLETE",
        "directories": directories,
        "untracked_large_files": untracked_large,
        "potentially_sensitive_paths": sorted(set(sensitive)),
        "date_pinned_scripts": sorted(set(date_pinned)),
        "rebuildable_artifacts": sorted(set(rebuildable)),
        "permission_denied": denied,
        "git_count_objects": git_objects.stdout.splitlines(),
        "recommended_actions": [
            "Review only; do not delete or move artifacts during this phase.",
            "Hash immutable business artifacts before retention changes.",
            "Resolve permission-denied paths before claiming a complete audit.",
            "Run git maintenance only after a separate approved backup and recovery plan.",
        ],
    }


def safe_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "directories": len(report["directories"]),
        "permission_denied": len(report["permission_denied"]),
        "report": safe_relative(args.output),
    }, ensure_ascii=False))
    return 2 if report["permission_denied"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
