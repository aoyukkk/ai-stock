from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_immutable_text(path: Path, content: str) -> str:
    encoded = content.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"IMMUTABLE_ARTIFACT_CONFLICT:{path.name}")
        return digest
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(encoded)
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
        temporary.unlink(missing_ok=True)
        raise ValueError("ARTIFACT_WRITE_INTEGRITY_FAILED")
    temporary.replace(path)
    return digest
