from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from temporal.freshness import canonical_hash, require_decision_as_of_time


def news_evidence_status(
    *,
    decision_as_of_time: datetime,
    provider_enabled: bool,
    provider_query_succeeded: bool,
    evidence: Iterable[Mapping[str, Any]],
    window_hours: int = 36,
    fallback_used: bool = False,
) -> dict[str, Any]:
    decision = require_decision_as_of_time(decision_as_of_time)
    if not provider_enabled:
        status = "NEWS_PROVIDER_DISABLED"
    elif not provider_query_succeeded:
        status = (
            "FLASH_V4_DIRECT_SEARCH_FALLBACK"
            if fallback_used
            else "NEWS_EVIDENCE_UNAVAILABLE"
        )
    else:
        status = "VERIFIED_EMPTY"
    accepted, rejected = [], []
    cutoff = decision - timedelta(hours=window_hours)
    for original in evidence:
        row = dict(original)
        value = row.get("published_at")
        try:
            published = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            row["exclusion_reason"] = "PUBLISH_TIME_UNKNOWN"
            rejected.append(row)
            continue
        if published.tzinfo is None:
            row["exclusion_reason"] = "PUBLISH_TIME_NOT_TIMEZONE_AWARE"
            rejected.append(row)
        elif published > decision:
            row["exclusion_reason"] = "FUTURE_NEWS"
            rejected.append(row)
        elif published < cutoff:
            row["exclusion_reason"] = "OLDER_THAN_WINDOW"
            rejected.append(row)
        else:
            accepted.append(row)
    if accepted:
        status = "VERIFIED_EVIDENCE" if provider_query_succeeded else status
    result = {
        "status": status,
        "decision_as_of_time": decision.isoformat(),
        "window_start": cutoff.isoformat(),
        "window_end": decision.isoformat(),
        "provider_enabled": provider_enabled,
        "provider_query_succeeded": provider_query_succeeded,
        "fallback_used": fallback_used,
        "evidence_count": len(accepted),
        "rejected_count": len(rejected),
        "confidence": 1.0 if provider_query_succeeded else (0.35 if fallback_used else 0.0),
        "eligible_for_verified_news_claim": provider_query_succeeded,
        "accepted": accepted,
        "rejected": rejected,
    }
    result["content_hash"] = canonical_hash(result)
    return result


def overseas_evidence_status(
    *,
    decision_as_of_time: datetime,
    provider_enabled: bool,
    is_mock: bool,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    decision = require_decision_as_of_time(decision_as_of_time)
    status = (
        "OVERSEAS_PROVIDER_DISABLED"
        if not provider_enabled
        else "OVERSEAS_MOCK_DATA_EXCLUDED"
        if is_mock
        else "VERIFIED_EVIDENCE"
    )
    result = {
        "status": status,
        "decision_as_of_time": decision.isoformat(),
        "provider_enabled": provider_enabled,
        "is_mock": is_mock,
        "eligible_for_scoring": provider_enabled and not is_mock,
        "confidence": 1.0 if provider_enabled and not is_mock else 0.0,
        "evidence": dict(evidence or {}) if provider_enabled and not is_mock else {},
    }
    result["content_hash"] = canonical_hash(result)
    return result
