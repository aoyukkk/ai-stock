from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"


if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@dataclass
class CheckReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def add_info(self, message: str) -> None:
        self.info.append(message)


def print_report(report: CheckReport) -> None:
    for item in report.info:
        print(f"[OK] {item}")
    for item in report.warnings:
        print(f"[WARN] {item}")
    for item in report.errors:
        print(f"[ERROR] {item}")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_value(root: Path, key: str) -> str | None:
    env_path = root / ".env"
    values = read_env_file(env_path if env_path.exists() else root / ".env.example")
    return values.get(key) or os.getenv(key)


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    return data if isinstance(data, dict) else {}


def is_false(value: object) -> bool:
    return str(value).strip().lower() in {"false", "0", "no", "off", ""}


def is_true(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def which(command: str) -> str | None:
    return shutil.which(command)


def run_command(command: list[str], cwd: Path | None = None) -> int:
    print(f"$ {' '.join(command)}")
    return subprocess.call(command, cwd=str(cwd or ROOT_DIR))


def iter_files(
    roots: Iterable[Path],
    suffixes: set[str] | None = None,
    excluded_parts: set[str] | None = None,
):
    excluded = excluded_parts or {
        ".git",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        "dist",
        "dist-electron",
        "release",
        "data",
        "logs",
    }
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            paths = [root]
        else:
            paths = root.rglob("*")
        for path in paths:
            if not path.is_file():
                continue
            if any(part in excluded for part in path.parts):
                continue
            if suffixes and path.suffix.lower() not in suffixes:
                continue
            yield path


def has_secret_like_literal(text: str) -> bool:
    patterns = (
        r"sk-[A-Za-z0-9_\-]{20,}",
        r"AKIA[0-9A-Z]{16}",
        r"xox[baprs]-[A-Za-z0-9\-]{20,}",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def npm_command() -> str | None:
    return which("npm.cmd") or which("npm")


def npx_command() -> str | None:
    return which("npx.cmd") or which("npx")
