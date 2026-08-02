from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from temporal.freshness import canonical_hash, require_decision_as_of_time


RISK_EVIDENCE_VERSION = "risk-evidence-lineage-v1"
RISK_COMPONENTS = (
    "liquidity_health",
    "downside_volatility",
    "max_drawdown",
    "high_position_crowding",
    "chip_risk",
    "unlock_risk",
    "reduction_risk",
    "pledge_risk",
    "major_financial_event_risk",
    "gap_limit_path_risk",
)


def filter_events_as_of(
    events: Iterable[Mapping[str, Any]],
    *,
    decision_as_of_time: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decision = require_decision_as_of_time(decision_as_of_time)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for original in events:
        row = dict(original)
        published = row.get("published_at") or row.get("ann_date") or row.get("f_ann_date")
        try:
            published_at = published if isinstance(published, datetime) else datetime.fromisoformat(str(published))
        except (TypeError, ValueError):
            row["exclusion_reason"] = "PUBLISH_TIME_UNKNOWN"
            rejected.append(row)
            continue
        if published_at.tzinfo is None:
            row["exclusion_reason"] = "PUBLISH_TIME_NOT_TIMEZONE_AWARE"
            rejected.append(row)
            continue
        if published_at > decision:
            row["exclusion_reason"] = "FUTURE_EVENT_EVIDENCE"
            rejected.append(row)
            continue
        event_id = str(row.get("event_id") or canonical_hash(row))
        if event_id in seen:
            row["exclusion_reason"] = "DUPLICATE_EVENT"
            rejected.append(row)
            continue
        seen.add(event_id)
        row["event_id"] = event_id
        row["published_at"] = published_at.isoformat()
        accepted.append(row)
    return accepted, rejected


def build_risk_evidence_lineage(
    *,
    stock_code: str,
    decision_as_of_time: datetime,
    components: Mapping[str, Mapping[str, Any] | None],
    event_evidence: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    decision = require_decision_as_of_time(decision_as_of_time)
    events, rejected_events = filter_events_as_of(
        event_evidence, decision_as_of_time=decision
    )
    rows: list[dict[str, Any]] = []
    for name in RISK_COMPONENTS:
        source = dict(components.get(name) or {})
        raw = source.get("raw_value")
        missing = raw is None
        rows.append(
            {
                "stock_code": stock_code,
                "component": name,
                "decision_as_of_time": decision.isoformat(),
                "raw_value": raw,
                "score": None if missing else source.get("score"),
                "contribution": None if missing else source.get("contribution"),
                "normalization_direction": source.get("normalization_direction"),
                "source": source.get("source"),
                "data_business_date": source.get("data_business_date"),
                "source_available_at": source.get("source_available_at"),
                "fetched_at": source.get("fetched_at"),
                "freshness_status": source.get("freshness_status", "MISSING" if missing else "UNVERIFIED"),
                "point_in_time_safe": bool(source.get("point_in_time_safe", False)),
                "confidence": 0.0 if missing else float(source.get("confidence", 0.0)),
                "missing_status": "UNKNOWN_MISSING_NOT_LOW_RISK" if missing else "OBSERVED",
                "fallback": source.get("fallback"),
                "version": RISK_EVIDENCE_VERSION,
            }
        )
    payload = {
        "stock_code": stock_code,
        "decision_as_of_time": decision.isoformat(),
        "version": RISK_EVIDENCE_VERSION,
        "rows": rows,
        "event_evidence": events,
        "rejected_event_evidence": rejected_events,
        "missing_component_count": sum(row["raw_value"] is None for row in rows),
        "point_in_time_safe": all(
            row["point_in_time_safe"] for row in rows if row["raw_value"] is not None
        ) and not any(row.get("exclusion_reason") == "FUTURE_EVENT_EVIDENCE" for row in rejected_events),
    }
    payload["content_hash"] = canonical_hash(payload)
    return payload
