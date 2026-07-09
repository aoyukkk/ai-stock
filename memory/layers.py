from __future__ import annotations

from datetime import datetime, timedelta, timezone


MEMORY_TYPES = {
    "short_term",
    "mid_term",
    "long_term",
    "episodic",
    "reflection",
    "procedural",
    "graph_reference",
}

MEMORY_LAYERS = {
    "market",
    "industry",
    "stock",
    "event",
    "strategy",
}

CONFLICT_STATUSES = {
    "NORMAL",
    "CONFLICTED",
    "INVALIDATED",
    "NEED_REVIEW",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def default_valid_until(memory_type: str, config) -> datetime | None:
    normalized = memory_type.lower()
    if normalized == "short_term":
        ttl_hours = int(config.short_term.get("ttl_hours", 24))
        return now_utc() + timedelta(hours=ttl_hours)
    if normalized == "mid_term":
        ttl_days = int(config.mid_term.get("ttl_days", 20))
        return now_utc() + timedelta(days=ttl_days)
    return None


def tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return {item for item in normalized.split() if len(item) >= 2}
