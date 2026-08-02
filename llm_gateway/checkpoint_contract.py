from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, field_validator

from temporal.freshness import canonical_hash


CHECKPOINT_CONTRACT_VERSION = "llm-checkpoint-contract-v2"


class LLMCheckpointContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_date: str
    stock_code: str
    stage: str
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
    checkpoint_contract_version: str = CHECKPOINT_CONTRACT_VERSION

    @field_validator("decision_as_of_time")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("checkpoint decision_as_of_time must be timezone-aware")
        return value

    @property
    def checkpoint_hash(self) -> str:
        return canonical_hash(self)


def checkpoint_reuse_audit(
    saved: Mapping[str, Any] | None,
    expected: LLMCheckpointContract,
) -> dict[str, Any]:
    saved = dict(saved or {})
    saved_contract = saved.get("checkpoint_contract")
    reasons: list[str] = []
    if saved.get("execution_status") != "SUCCESS":
        reasons.append("CHECKPOINT_NOT_SUCCESSFUL")
    if not isinstance(saved_contract, Mapping):
        reasons.append("LEGACY_CHECKPOINT_INSUFFICIENT_METADATA")
    else:
        expected_payload = expected.model_dump(mode="json")
        for field, expected_value in expected_payload.items():
            if saved_contract.get(field) != expected_value:
                reasons.append(f"{field.upper()}_MISMATCH")
        if saved.get("checkpoint_contract_hash") != expected.checkpoint_hash:
            reasons.append("CHECKPOINT_CONTRACT_HASH_MISMATCH")
    allowed = not reasons
    return {
        "stock_code": expected.stock_code,
        "stage": expected.stage,
        "reuse_allowed": allowed,
        "reasons": sorted(set(reasons)),
        "expected_checkpoint_contract_hash": expected.checkpoint_hash,
        "saved_checkpoint_contract_hash": saved.get("checkpoint_contract_hash"),
        "reused_input_hash_match": bool(
            saved_contract
            and saved_contract.get("input_hash") == expected.input_hash
        ),
    }
