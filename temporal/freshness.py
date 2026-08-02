from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


FRESHNESS_CONTRACT_VERSION = "point-in-time-freshness-v1"


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    VERIFIED_CURRENT = "VERIFIED_CURRENT"
    STALE = "STALE"
    UNVERIFIED = "UNVERIFIED"
    MISSING = "MISSING"
    DISABLED = "DISABLED"
    MOCK = "MOCK"
    DATA_CONFLICT = "DATA_CONFLICT"
    FUTURE_DATA_DETECTED = "FUTURE_DATA_DETECTED"
    HISTORICAL_MEMBERSHIP_UNVERIFIED = "HISTORICAL_MEMBERSHIP_UNVERIFIED"
    FLASH_V4_DIRECT_SEARCH_FALLBACK = "FLASH_V4_DIRECT_SEARCH_FALLBACK"


class FreshnessGateAction(StrEnum):
    ALLOW = "ALLOW"
    DEGRADE = "DEGRADE"
    BLOCK_STAGE = "BLOCK_STAGE"


class DataFreshnessEvidence(BaseModel):
    """One canonical, hashable freshness/PIT record for every external input."""

    model_config = ConfigDict(extra="forbid")

    dataset_name: str
    provider: str
    decision_as_of_time: datetime
    data_business_date: date | None = None
    source_available_at: datetime | None = None
    fetched_at: datetime | None = None
    cache_fetched_at: datetime | None = None
    cache_age_hours: float | None = Field(default=None, ge=0)
    cache_age_trading_days: int | None = Field(default=None, ge=0)
    max_age_hours: float | None = Field(default=None, ge=0)
    max_age_trading_days: int | None = Field(default=None, ge=0)
    freshness_status: FreshnessStatus
    gate_action: FreshnessGateAction
    eligible_for_scoring: bool
    point_in_time_safe: bool
    source_status: str
    confidence: float = Field(default=1.0, ge=0, le=1)
    is_mock: bool = False
    degradation_reason: str | None = None
    content_hash: str | None = None
    contract_version: str = FRESHNESS_CONTRACT_VERSION

    @field_validator(
        "decision_as_of_time", "source_available_at", "fetched_at", "cache_fetched_at"
    )
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("freshness timestamps must be timezone-aware")
        return value


class FreshnessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_name: str
    max_age_hours: float | None = Field(default=None, ge=0)
    max_age_trading_days: int | None = Field(default=None, ge=0)
    stale_action: FreshnessGateAction = FreshnessGateAction.BLOCK_STAGE
    unverified_action: FreshnessGateAction = FreshnessGateAction.DEGRADE
    missing_action: FreshnessGateAction = FreshnessGateAction.BLOCK_STAGE
    refresh_on_stale: bool = True
    historical_current_membership_allowed: bool = False


def require_decision_as_of_time(value: datetime | None) -> datetime:
    if value is None:
        raise ValueError("DECISION_AS_OF_TIME_REQUIRED")
    if value.tzinfo is None:
        raise ValueError("DECISION_AS_OF_TIME_MUST_BE_TIMEZONE_AWARE")
    return value


def canonical_hash(payload: Any) -> str:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    material = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def trading_day_age(business_date: date | None, decision_date: date) -> int | None:
    if business_date is None:
        return None
    if business_date > decision_date:
        return 0
    cursor = business_date
    count = 0
    while cursor < decision_date:
        cursor = date.fromordinal(cursor.toordinal() + 1)
        if cursor.weekday() < 5:
            count += 1
    return count


def evaluate_freshness(
    *,
    dataset_name: str,
    provider: str,
    decision_as_of_time: datetime,
    policy: FreshnessPolicy,
    data_business_date: date | None = None,
    source_available_at: datetime | None = None,
    fetched_at: datetime | None = None,
    cache_fetched_at: datetime | None = None,
    source_status: str = "available",
    content: Any = None,
    is_mock: bool = False,
    current_membership_only: bool = False,
) -> DataFreshnessEvidence:
    decision = require_decision_as_of_time(decision_as_of_time)
    observed_at = cache_fetched_at or fetched_at
    age_hours = (
        max(0.0, (decision.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds() / 3600)
        if observed_at is not None and observed_at <= decision
        else None
    )
    business_age_days = trading_day_age(data_business_date, decision.date())
    observed_business_date = (
        observed_at.astimezone(decision.tzinfo).date() if observed_at is not None else None
    )
    observed_age_days = trading_day_age(observed_business_date, decision.date())
    age_days = max(
        (
            item
            for item in (business_age_days, observed_age_days)
            if item is not None
        ),
        default=None,
    )
    future = (
        (source_available_at is not None and source_available_at > decision)
        or (data_business_date is not None and data_business_date > decision.date())
        or (
            current_membership_only
            and observed_at is not None
            and observed_at.astimezone(decision.tzinfo).date() > decision.date()
        )
    )

    reason: str | None = None
    if future:
        status = FreshnessStatus.FUTURE_DATA_DETECTED
        action = FreshnessGateAction.BLOCK_STAGE
    elif is_mock:
        status = FreshnessStatus.MOCK
        action = FreshnessGateAction.DEGRADE
        reason = "MOCK_DATA_EXCLUDED_FROM_SCORING"
    elif source_status.lower() in {"disabled"}:
        status = FreshnessStatus.DISABLED
        action = FreshnessGateAction.DEGRADE
        reason = "DATA_SOURCE_DISABLED"
    elif source_status.lower() in {"missing", "empty", "error", "failed", "unavailable"}:
        status = FreshnessStatus.MISSING
        action = policy.missing_action
        reason = "DATASET_MISSING"
    elif (
        policy.max_age_hours is not None
        and age_hours is not None
        and age_hours > policy.max_age_hours
    ) or (
        policy.max_age_trading_days is not None
        and age_days is not None
        and age_days > policy.max_age_trading_days
    ):
        status = FreshnessStatus.STALE
        action = policy.stale_action
        reason = "CACHE_OR_BUSINESS_DATE_EXCEEDS_FRESHNESS_POLICY"
    elif current_membership_only and data_business_date and data_business_date < decision.date():
        status = FreshnessStatus.HISTORICAL_MEMBERSHIP_UNVERIFIED
        action = (
            FreshnessGateAction.DEGRADE
            if policy.historical_current_membership_allowed
            else FreshnessGateAction.BLOCK_STAGE
        )
        reason = "CURRENT_MEMBERSHIP_CANNOT_PROVE_HISTORICAL_MEMBERSHIP"
    elif observed_at is None and data_business_date is None:
        status = FreshnessStatus.UNVERIFIED
        action = policy.unverified_action
        reason = "NO_VERIFIABLE_SOURCE_TIMESTAMP"
    else:
        status = FreshnessStatus.FRESH
        action = FreshnessGateAction.ALLOW

    pit_safe = status is not FreshnessStatus.FUTURE_DATA_DETECTED and not (
        current_membership_only
        and status is FreshnessStatus.HISTORICAL_MEMBERSHIP_UNVERIFIED
    )
    eligible = (
        action is not FreshnessGateAction.BLOCK_STAGE
        and pit_safe
        and not is_mock
        and status in {FreshnessStatus.FRESH, FreshnessStatus.VERIFIED_CURRENT}
    )
    confidence = 1.0 if status in {FreshnessStatus.FRESH, FreshnessStatus.VERIFIED_CURRENT} else (
        0.5 if action is FreshnessGateAction.DEGRADE else 0.0
    )
    return DataFreshnessEvidence(
        dataset_name=dataset_name,
        provider=provider,
        decision_as_of_time=decision,
        data_business_date=data_business_date,
        source_available_at=source_available_at,
        fetched_at=fetched_at,
        cache_fetched_at=cache_fetched_at,
        cache_age_hours=age_hours,
        cache_age_trading_days=age_days,
        max_age_hours=policy.max_age_hours,
        max_age_trading_days=policy.max_age_trading_days,
        freshness_status=status,
        gate_action=action,
        eligible_for_scoring=eligible,
        point_in_time_safe=pit_safe,
        source_status=source_status,
        confidence=confidence,
        is_mock=is_mock,
        degradation_reason=reason,
        content_hash=canonical_hash(content) if content is not None else None,
    )


def build_freshness_manifest(
    *,
    run_id: str,
    decision_as_of_time: datetime,
    evidence: list[DataFreshnessEvidence],
) -> dict[str, Any]:
    decision = require_decision_as_of_time(decision_as_of_time)
    rows = [item.model_dump(mode="json") for item in evidence]
    issues = [
        row
        for row in rows
        if row["freshness_status"] not in {"FRESH", "VERIFIED_CURRENT"}
    ]
    blocked = [
        row for row in rows if row["gate_action"] == FreshnessGateAction.BLOCK_STAGE
    ]
    payload = {
        "run_id": run_id,
        "decision_as_of_time": decision.isoformat(),
        "contract_version": FRESHNESS_CONTRACT_VERSION,
        "datasets": rows,
        "issue_count": len(issues),
        "blocked_dataset_count": len(blocked),
        "point_in_time_safe": all(row["point_in_time_safe"] for row in rows),
        "eligible_for_scoring": all(row["eligible_for_scoring"] for row in rows),
        "stage_may_continue": not blocked,
    }
    payload["manifest_hash"] = canonical_hash(payload)
    return payload


def compatibility_as_of_time(
    payload: Mapping[str, Any], *, decision_as_of_time: datetime
) -> dict[str, Any]:
    """Keep old clients working without letting publication time masquerade as data time."""
    output = dict(payload)
    output["as_of_time"] = require_decision_as_of_time(decision_as_of_time).isoformat()
    output["as_of_time_semantics"] = "DEPRECATED_ALIAS_OF_DECISION_AS_OF_TIME"
    return output
