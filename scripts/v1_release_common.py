from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)\b[A-Z]:\\[^\r\n\"']+")
UNIX_USER_PATH = re.compile(r"/(?:Users|home)/[^/\s]+/")
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:authorization|api[_-]?key|password|client[_-]?secret)\s*[:=]\s*[^\s,;\"']+"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def sanitize_value(value: Any, *, source_root: Path, secrets: Iterable[str]) -> tuple[Any, int, int]:
    secret_count = 0
    path_count = 0
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            sanitized, secret_hits, path_hits = sanitize_value(item, source_root=source_root, secrets=secrets)
            result[key] = sanitized
            secret_count += secret_hits
            path_count += path_hits
        return result, secret_count, path_count
    if isinstance(value, list):
        result = []
        for item in value:
            sanitized, secret_hits, path_hits = sanitize_value(item, source_root=source_root, secrets=secrets)
            result.append(sanitized)
            secret_count += secret_hits
            path_count += path_hits
        return result, secret_count, path_count
    if not isinstance(value, str):
        return value, 0, 0

    text = value
    for secret in secrets:
        if secret and secret in text:
            secret_count += text.count(secret)
            text = text.replace(secret, "[REDACTED]")

    root_variants = {str(source_root), source_root.as_posix()}
    for root in sorted(root_variants, key=len, reverse=True):
        if root and root.lower() in text.lower():
            pattern = re.compile(re.escape(root), re.IGNORECASE)
            path_count += len(pattern.findall(text))
            text = pattern.sub("", text).lstrip("\\/")

    matches = WINDOWS_ABSOLUTE_PATH.findall(text)
    if matches:
        path_count += len(matches)
        text = WINDOWS_ABSOLUTE_PATH.sub("[REDACTED_PATH]", text)
    matches = UNIX_USER_PATH.findall(text)
    if matches:
        path_count += len(matches)
        text = UNIX_USER_PATH.sub("/[REDACTED_USER]/", text)

    assignment_hits = SECRET_ASSIGNMENT.findall(text)
    if assignment_hits:
        secret_count += len(assignment_hits)
        text = SECRET_ASSIGNMENT.sub("secret=[REDACTED]", text)
    return text, secret_count, path_count


def scan_text(text: str, known_secrets: Iterable[str]) -> dict[str, int]:
    secret_hits = sum(text.count(secret) for secret in known_secrets if secret)
    secret_hits += len(SECRET_ASSIGNMENT.findall(text))
    path_hits = len(WINDOWS_ABSOLUTE_PATH.findall(text)) + len(UNIX_USER_PATH.findall(text))
    return {"secret_hits": secret_hits, "absolute_path_hits": path_hits}

