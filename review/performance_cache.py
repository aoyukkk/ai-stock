from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class PerformanceCacheManager:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or ROOT_DIR / "data" / "cache" / "selection_performance"

    def write(self, run_id: str, metadata: dict[str, Any], payload: dict[str, Any]) -> tuple[str, int, Path]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        row_count = sum(len(payload.get(key, [])) for key in ("cohorts", "daily", "stocks"))
        content = {
            **metadata,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "row_count": row_count,
            "payload": payload,
        }
        checksum = stable_hash(content)
        envelope = {**content, "checksum": checksum}
        target = self.cache_dir / f"{run_id}.json"
        temporary = self.cache_dir / f".{run_id}.{os.getpid()}.tmp"
        temporary.write_text(json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
        verified = json.loads(temporary.read_text(encoding="utf-8"))
        check_content = {key: value for key, value in verified.items() if key != "checksum"}
        if verified.get("row_count") != row_count or stable_hash(check_content) != checksum:
            temporary.unlink(missing_ok=True)
            raise ValueError("PERFORMANCE_CACHE_INTEGRITY_FAILED")
        temporary.replace(target)
        return checksum, row_count, target

    def validate(self, path: Path, expected_checksum: str, expected_row_count: int) -> bool:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        checksum = value.pop("checksum", None)
        return checksum == expected_checksum == stable_hash(value) and value.get("row_count") == expected_row_count
