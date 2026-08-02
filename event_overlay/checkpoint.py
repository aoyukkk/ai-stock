from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator

from event_overlay.hashing import canonical_hash


CHECKPOINT_CONTRACT_VERSION = "EVENT_OVERLAY_CHECKPOINT_CONTRACT_V2_RESULT_HASH"


class EventOverlayCheckpointContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_date: str
    stock_code: str
    factor_version: str
    input_hash: str
    prompt_version: str
    prompt_hash: str
    schema_version: str
    contract_version: str
    data_manifest_hash: str
    membership_manifest_hash: str
    fundamental_snapshot_hash: str
    news_snapshot_hash: str
    overseas_snapshot_hash: str
    market_regime_hash: str
    risk_version: str
    decision_as_of_time: datetime
    search_mode: str
    search_query_hash: str
    screening_config_hash: str
    source_tier_policy_hash: str
    checkpoint_contract_version: str = CHECKPOINT_CONTRACT_VERSION

    @field_validator("decision_as_of_time")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("CHECKPOINT_TIMEZONE_REQUIRED")
        return value

    @property
    def checkpoint_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.payload = self._load()

    def audit(self, contract: EventOverlayCheckpointContract) -> dict[str, Any]:
        saved = self.payload.get(contract.stock_code)
        reasons: list[str] = []
        saved_result = (saved or {}).get("result") or {}
        if (
            not saved
            or saved.get("execution_status") != "SUCCESS"
            or saved_result.get("search_status") in {"SEARCH_FAILED", "SEARCH_TIMEOUT"}
        ):
            reasons.append("CHECKPOINT_NOT_SUCCESSFUL")
        else:
            if saved.get("result_hash") != canonical_hash(saved_result):
                reasons.append("CHECKPOINT_RESULT_HASH_MISMATCH")
            try:
                from event_overlay.schemas import EventReviewResult

                validated = EventReviewResult.model_validate(saved_result)
            except Exception:
                reasons.append("CHECKPOINT_RESULT_SCHEMA_INVALID")
            else:
                if validated.stock_code != contract.stock_code:
                    reasons.append("CHECKPOINT_RESULT_STOCK_CODE_MISMATCH")
                if validated.review_version != contract.schema_version:
                    reasons.append("CHECKPOINT_RESULT_REVIEW_VERSION_MISMATCH")
            saved_contract = saved.get("checkpoint_contract") or {}
            for key, value in contract.model_dump(mode="json").items():
                if saved_contract.get(key) != value:
                    reasons.append(f"{key.upper()}_MISMATCH")
            if saved.get("checkpoint_contract_hash") != contract.checkpoint_hash:
                reasons.append("CHECKPOINT_CONTRACT_HASH_MISMATCH")
        return {
            "reuse_allowed": not reasons,
            "reasons": sorted(set(reasons)),
            "stale": bool(saved and reasons),
            "saved": saved,
            "reused_input_hash_match": bool(
                saved and (saved.get("checkpoint_contract") or {}).get("input_hash") == contract.input_hash
            ),
        }

    def save(self, contract: EventOverlayCheckpointContract, result: dict[str, Any]) -> None:
        execution_status = (
            "FAILED"
            if result.get("search_status") in {"SEARCH_FAILED", "SEARCH_TIMEOUT"}
            else "SUCCESS"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(
            self.path.with_name(self.path.name + ".write.lock"),
            timeout_seconds=15,
            stale_after_seconds=300,
        ):
            # Another process may have saved a different stock after this
            # instance was constructed. Re-read and merge while holding the
            # lock instead of overwriting its checkpoint.
            merged = self._load()
            merged[contract.stock_code] = {
                "execution_status": execution_status,
                "checkpoint_contract": contract.model_dump(mode="json"),
                "checkpoint_contract_hash": contract.checkpoint_hash,
                "result_hash": canonical_hash(result),
                "result": result,
            }
            temporary = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(
                        merged,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                    encoding="utf-8",
                )
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.payload = merged

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}


@contextmanager
def exclusive_file_lock(
    path: Path,
    *,
    timeout_seconds: float,
    stale_after_seconds: float,
):
    """Small cross-process lock based on atomic file creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
                if age > stale_after_seconds:
                    path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError(f"EVENT_OVERLAY_LOCK_TIMEOUT:{path.name}")
            time.sleep(0.05)
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)
