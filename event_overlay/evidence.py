from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
import math
import re
from typing import Any
import unicodedata
from urllib.parse import urlsplit

from event_overlay.constants import DIRECT_SEARCH_FALLBACK
from event_overlay.hashing import canonical_hash
from event_overlay.schemas import EventEvidenceItem, EventReviewResult, RiskAction
from research.evidence import canonicalize_url


def normalize_search_result(
    stock: dict[str, Any],
    raw: dict[str, Any],
    *,
    decision_as_of_time: datetime,
    max_events: int,
    confidence_discount: float,
    freshness_policy: dict[str, Any] | None = None,
    source_tier_policy: dict[str, Any] | None = None,
) -> EventReviewResult:
    code = str(stock["stock_code"])
    raw_status = str(raw.get("status") or "SEARCH_FAILED")
    status = {
        "STRUCTURED_VERIFIED": "VERIFIED_WITH_STRUCTURED_PROVIDER",
        "NO_SEARCH_RESULT": "NO_RESULT_FOUND",
    }.get(raw_status, raw_status)
    provider = str(raw.get("provider") or "UNKNOWN")
    direct = bool(raw.get("direct_search_used"))
    provider_verified = bool(raw.get("provider_verified"))
    warnings: list[str] = []
    conflicts: list[str] = []
    items: list[EventEvidenceItem] = []
    normalized_items: list[EventEvidenceItem] = []
    effective_freshness_policy = _resolve_freshness_policy(freshness_policy)
    effective_source_tier_policy = _resolve_source_tier_policy(source_tier_policy)

    for index, candidate in enumerate(list(raw.get("items") or [])):
        normalized, issue = _normalize_item(
            code,
            candidate,
            provider=provider,
            provider_verified=provider_verified,
            decision_as_of_time=decision_as_of_time,
            index=index,
            discount=confidence_discount if direct else 1.0,
            freshness_policy=effective_freshness_policy,
            source_tier_policy=effective_source_tier_policy,
        )
        if issue:
            warnings.append(issue)
        if normalized is None:
            continue
        reported_tier = normalized.raw_metadata.get("reported_source_tier")
        if reported_tier and reported_tier != normalized.source_tier:
            warnings.append("SOURCE_TIER_OVERRIDDEN_FROM_URL")
        normalized_items.append(normalized)
    deduplicated = _deduplicate_items(normalized_items)
    if len(deduplicated) < len(normalized_items):
        warnings.append("DUPLICATE_EVIDENCE_COLLAPSED")
    items = sorted(
        deduplicated,
        key=lambda item: (
            item.score_eligible,
            item.materiality * item.relevance * item.confidence * (item.time_decay or 0.0),
            item.published_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )[:max_events]

    if direct and status == DIRECT_SEARCH_FALLBACK:
        status = "MATERIAL_EVENT_FOUND"
    if not items and status not in {
        "SEARCH_FAILED",
        "SEARCH_TIMEOUT",
        "HISTORICAL_EVIDENCE_UNAVAILABLE",
        "NO_MATERIAL_EVENT",
        "NO_RESULT_FOUND",
    }:
        status = (
            "NO_MATERIAL_EVENT"
            if raw.get("search_completed") or direct
            else "NO_RESULT_FOUND"
        )
    eligible_items = [item for item in items if item.score_eligible]
    if items and not eligible_items:
        warnings.append("NO_SCORE_ELIGIBLE_EVIDENCE")
        if status not in {"SEARCH_FAILED", "SEARCH_TIMEOUT"}:
            status = "NO_SCORE_ELIGIBLE_EVIDENCE"
    directions = {
        item.event_direction
        for item in eligible_items
        if item.event_direction != "NEUTRAL"
    }
    if "POSITIVE" in directions and "NEGATIVE" in directions:
        status = "EVIDENCE_CONFLICT"
        conflicts.append("POSITIVE_AND_NEGATIVE_MATERIAL_EVENTS")

    opportunity = _opportunity(eligible_items)
    confidence = _confidence(eligible_items)
    breadth = min(1.0, len({item.domain for item in eligible_items if item.domain}) / 3)
    risk_action = _risk_action(eligible_items, conflicts)
    requires_pro_review = (
        bool(conflicts)
        or risk_action in {RiskAction.BLOCK, RiskAction.WATCH_ONLY}
        or abs(opportunity) >= 2
        or any(item.source_tier == "tier_4" for item in eligible_items)
    )
    return EventReviewResult(
        stock_code=code,
        resolved_stock_name=(
            str(raw.get("stock_name") or "").strip() or None
        ),
        search_status=status,
        material_events=items,
        event_opportunity_score=opportunity,
        evidence_confidence=confidence,
        breadth_score=breadth,
        risk_action=risk_action,
        conflicts=conflicts,
        warnings=sorted(set(warnings)),
        requires_pro_review=requires_pro_review,
        direct_search_used=direct,
        provider_verified=provider_verified,
        confidence_discount_applied=direct,
        production_eligible=False,
        shadow_eligible=True,
    )


def _normalize_item(
    code: str,
    candidate: dict[str, Any],
    *,
    provider: str,
    provider_verified: bool,
    decision_as_of_time: datetime,
    index: int,
    discount: float,
    freshness_policy: dict[str, Any],
    source_tier_policy: dict[str, tuple[str, ...]],
) -> tuple[EventEvidenceItem | None, str | None]:
    url = str(candidate.get("url") or "").strip() or None
    canonical_url = None
    domain = None
    issue = None
    if url:
        try:
            canonical_url = canonicalize_url(url)
            domain = (urlsplit(canonical_url).hostname or "").lower()
        except ValueError:
            issue = "URL_MISSING"
            url = None
    else:
        issue = "URL_MISSING"
    published_at = _parse_time(candidate.get("published_at"))
    temporal_status = "CURRENT"
    score_eligible = True
    score_exclusion_reasons: list[str] = []
    invalid_numeric_fields = {
        str(field)
        for field in (candidate.get("_invalid_numeric_fields") or [])
    }
    invalid_numeric_fields.update(
        field
        for field in ("materiality", "relevance", "confidence")
        if not _is_finite_numeric_input(candidate.get(field))
    )
    if invalid_numeric_fields:
        score_eligible = False
        score_exclusion_reasons.append("INVALID_NUMERIC_EVIDENCE")
        issue = issue or "INVALID_NUMERIC_EVIDENCE"
    if canonical_url is None:
        score_eligible = False
        score_exclusion_reasons.append("EVIDENCE_URL_INVALID_OR_MISSING")
    if published_at is None:
        temporal_status = "PUBLISH_TIME_UNKNOWN"
        issue = issue or temporal_status
        score_eligible = False
        score_exclusion_reasons.append("PUBLISH_TIME_UNKNOWN")
    elif published_at > decision_as_of_time:
        return None, "FUTURE_EVIDENCE_REJECTED"
    event_type = str(candidate.get("event_type") or "OTHER").upper()
    source_type = str(candidate.get("source_type") or "UNKNOWN").upper()
    derived_source_tier = _derive_source_tier(domain, source_tier_policy)
    freshness_window_hours = _freshness_window_hours(
        event_type=event_type,
        source_type=source_type,
        source_tier=derived_source_tier,
        policy=freshness_policy,
    )
    age_hours = (
        max(0.0, (decision_as_of_time - published_at).total_seconds() / 3600)
        if published_at else None
    )
    if age_hours is not None and age_hours > freshness_window_hours:
        temporal_status = "FRESHNESS_WINDOW_EXCEEDED"
        issue = issue or temporal_status
        score_eligible = False
        score_exclusion_reasons.append("FRESHNESS_WINDOW_EXCEEDED")
    direction = str(candidate.get("event_direction") or "NEUTRAL").upper()
    if direction not in {"POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"}:
        direction = "NEUTRAL"
    content_material = {
        "stock_code": code,
        "url": canonical_url,
        "title": str(candidate.get("title") or ""),
        "summary": str(candidate.get("summary") or ""),
        "published_at": published_at,
    }
    content_hash = canonical_hash(content_material)
    cluster_material = {
        "stock_code": code,
        "event_type": event_type,
        "published_date": published_at.date().isoformat() if published_at else "UNKNOWN",
        "title_key": " ".join(str(candidate.get("title") or "").lower().split())[:120],
    }
    cluster_id = canonical_hash(cluster_material)[:32]
    retrieved_at = datetime.now(timezone.utc)
    raw_confidence = _unit_value(candidate.get("confidence"), default=0.3) or 0.0
    confidence = _unit_value(raw_confidence * discount, default=0.0)
    item = EventEvidenceItem(
        stock_code=code,
        event_id=f"evt-{content_hash[:24]}",
        event_cluster_id=f"cluster-{cluster_id}",
        event_type=event_type,
        title=str(candidate.get("title") or "未命名事件"),
        summary=str(candidate.get("summary") or "未提供摘要"),
        url=url,
        canonical_url=canonical_url,
        domain=domain,
        published_at=published_at,
        retrieved_at=retrieved_at,
        source_tier=derived_source_tier,
        source_type=source_type,
        provider=provider,
        provider_verified=provider_verified,
        event_direction=direction,
        direction={"POSITIVE": 1.0, "NEGATIVE": -1.0, "NEUTRAL": 0.0, "MIXED": 0.0}[direction],
        impact_magnitude=_optional_unit(candidate.get("impact_magnitude")),
        materiality=_unit_value(candidate.get("materiality"), default=0.3),
        relevance=_unit_value(candidate.get("relevance"), default=0.5),
        confidence=confidence,
        named_company=candidate.get("named_company"),
        directness=_optional_unit(candidate.get("directness")),
        directness_reason=candidate.get("directness_reason"),
        exposure_estimate=_optional_unit(candidate.get("exposure_estimate")),
        exposure_confidence=_optional_unit(candidate.get("exposure_confidence")),
        exposure_evidence=candidate.get("exposure_evidence"),
        novelty=novelty_score(published_at, decision_as_of_time),
        price_already_reacted=_optional_unit(candidate.get("price_already_reacted")),
        confirmation_status=candidate.get("confirmation_status") or "NOT_AVAILABLE_AT_CUTOFF",
        a_share_breadth_confirmation=_optional_unit(candidate.get("a_share_breadth_confirmation")),
        crowding_status=str(candidate.get("crowding_status") or "UNKNOWN").upper(),
        contradiction_status=str(candidate.get("contradiction_status") or "NONE").upper(),
        decay_days=(
            max(0.0, (decision_as_of_time - published_at).total_seconds() / 86400)
            if published_at else None
        ),
        time_decay=novelty_score(published_at, decision_as_of_time),
        point_in_time_safe=published_at is not None,
        temporal_status=temporal_status,
        score_eligible=score_eligible,
        score_exclusion_reasons=score_exclusion_reasons,
        freshness_window_hours=freshness_window_hours,
        content_hash=content_hash,
        raw_metadata={
            "source_index": index,
            "reported_source_tier": candidate.get("source_tier"),
            "source_tier_derivation": "URL_DOMAIN_POLICY",
            "age_hours": round(age_hours, 6) if age_hours is not None else None,
            "invalid_numeric_fields": sorted(invalid_numeric_fields),
        },
    )
    return item, issue


def _resolve_freshness_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    """Return a validated scoring-window policy without trusting model output."""
    if policy is None:
        from event_overlay.config import load_event_overlay_config

        policy = load_event_overlay_config().get("freshness", {})
    defaults = {
        "market_news_hours": 36.0,
        "default_hours": 72.0,
        "announcement_hours": 168.0,
    }
    resolved: dict[str, Any] = {}
    for key, default in defaults.items():
        value = float(policy.get(key, default))
        if value <= 0:
            raise ValueError(f"FRESHNESS_POLICY_INVALID:{key}")
        resolved[key] = value
    source_type_defaults = {
        "announcement_source_types": (
            "ANNOUNCEMENT", "DISCLOSURE", "EXCHANGE", "REGULATORY", "FILING",
            "GOVERNMENT", "OFFICIAL", "交易所公告", "公司公告", "政府公告",
            "定期报告", "临时公告",
        ),
        "market_news_source_types": (
            "MARKET_NEWS", "MEDIA", "PRESS", "INDUSTRY_NEWS", "市场新闻",
            "媒体报道", "行业新闻",
        ),
    }
    for key, default in source_type_defaults.items():
        configured = policy.get(key, default)
        values = configured if isinstance(configured, (list, tuple, set)) else [configured]
        resolved[key] = tuple(
            str(value).strip().upper()
            for value in values
            if str(value).strip()
        )
    return resolved


def _resolve_source_tier_policy(
    policy: dict[str, Any] | None,
) -> dict[str, tuple[str, ...]]:
    """Normalize URL-domain policy; LLM-reported tiers are never accepted."""
    if policy is None:
        from event_overlay.config import load_event_overlay_config

        policy = load_event_overlay_config().get("source_tiers", {})
    resolved: dict[str, list[str]] = {f"tier_{level}": [] for level in range(1, 5)}
    for key, value in policy.items():
        if key in resolved:
            domains = value if isinstance(value, (list, tuple, set)) else [value]
            resolved[key].extend(str(domain).strip().lower().lstrip(".") for domain in domains)
            continue
        tier = str(value)
        if tier in resolved:
            resolved[tier].append(str(key).strip().lower().lstrip("."))
    return {
        tier: tuple(sorted({domain for domain in domains if domain and domain != "unknown"}))
        for tier, domains in resolved.items()
    }


def _derive_source_tier(
    domain: str | None,
    policy: dict[str, tuple[str, ...]],
) -> str:
    if not domain:
        return "tier_4"
    normalized = domain.lower().rstrip(".")
    for tier in ("tier_1", "tier_2", "tier_3", "tier_4"):
        if any(
            normalized == configured or normalized.endswith(f".{configured}")
            for configured in policy.get(tier, ())
        ):
            return tier
    return "tier_4"


def _freshness_window_hours(
    *,
    event_type: str,
    source_type: str,
    source_tier: str,
    policy: dict[str, Any],
) -> float:
    announcement_markers = {
        "ANNOUNCEMENT", "DISCLOSURE", "EXCHANGE", "REGULATORY", "FILING",
        "GOVERNMENT", "OFFICIAL", "公告", "交易所", "定期报告", "临时报告",
    }
    market_news_markers = {
        "NEWS", "MEDIA", "PRESS", "MARKET", "INDUSTRY", "SECTOR", "MACRO",
        "新闻", "媒体", "报道",
    }
    configured_announcements = set(policy.get("announcement_source_types", ()))
    configured_market_news = set(policy.get("market_news_source_types", ()))
    is_reported_announcement = any(
        marker in source_type for marker in configured_announcements
    ) or any(marker in source_type for marker in announcement_markers)
    # The extended 168-hour window is reserved for a locally verified official
    # URL.  A model cannot turn an old media article into an "announcement" by
    # self-reporting its source type.
    if is_reported_announcement and source_tier == "tier_1":
        return policy["announcement_hours"]
    if any(marker in source_type for marker in configured_market_news) or any(
        marker in source_type for marker in market_news_markers
    ) or event_type in {
        "POLICY", "INDUSTRY", "SECTOR", "MARKET", "MACRO",
    }:
        return policy["market_news_hours"]
    return policy["default_hours"]


def _deduplicate_items(items: list[EventEvidenceItem]) -> list[EventEvidenceItem]:
    """Collapse URL duplicates and conservative same-event reprints."""
    ranked = sorted(items, key=_evidence_quality_key, reverse=True)
    retained: list[EventEvidenceItem] = []
    for item in ranked:
        if any(_same_evidence(item, prior) for prior in retained):
            continue
        retained.append(item)
    return retained


def _evidence_quality_key(item: EventEvidenceItem) -> tuple[Any, ...]:
    tier_quality = {"tier_1": 4, "tier_2": 3, "tier_3": 2, "tier_4": 1}
    return (
        item.score_eligible,
        tier_quality[item.source_tier],
        item.provider_verified,
        item.confidence * item.materiality * item.relevance,
        item.published_at or datetime.min.replace(tzinfo=timezone.utc),
    )


def _same_evidence(left: EventEvidenceItem, right: EventEvidenceItem) -> bool:
    if left.canonical_url and left.canonical_url == right.canonical_url:
        return True
    if left.event_type != right.event_type:
        return False
    if left.published_at and right.published_at:
        if abs((left.published_at - right.published_at).total_seconds()) > 48 * 3600:
            return False
    elif left.published_at is not None or right.published_at is not None:
        return False
    left_title = _semantic_title(left.title)
    right_title = _semantic_title(right.title)
    if not left_title or not right_title:
        return False
    if left_title == right_title:
        return True
    shorter, longer = sorted((left_title, right_title), key=len)
    if len(shorter) >= 8 and shorter in longer and len(shorter) / len(longer) >= 0.7:
        return True
    return SequenceMatcher(None, left_title, right_title).ratio() >= 0.88


def _semantic_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = re.sub(r"(?:公告|快讯|最新|转载|公司)", "", normalized)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def _parse_time(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _opportunity(items: list[EventEvidenceItem]) -> float:
    values = []
    direction_value = {"POSITIVE": 1, "NEGATIVE": -1, "NEUTRAL": 0, "MIXED": 0}
    for item in items:
        values.append(
            direction_value[item.event_direction]
            * item.materiality
            * item.relevance
            * item.confidence
            * (item.time_decay or 0.0)
            * 3
        )
    return round(max(-3.0, min(3.0, sum(values))), 4)


def _confidence(items: list[EventEvidenceItem]) -> float:
    if not items:
        return 0.0
    tier_weight = {"tier_1": 1.0, "tier_2": 0.85, "tier_3": 0.65, "tier_4": 0.35}
    values = [item.confidence * tier_weight[item.source_tier] for item in items]
    independent_domains = len({item.domain for item in items if item.domain})
    source_bonus = max(0, independent_domains - 1) * 0.05
    return round(min(1.0, sum(values) / len(values) + source_bonus), 4)


def _risk_action(items: list[EventEvidenceItem], conflicts: list[str]) -> RiskAction:
    tier_a_severe = any(
        item.event_direction == "NEGATIVE"
        and item.materiality >= 0.85
        and item.source_tier == "tier_1"
        and bool(item.url)
        and item.provider_verified
        for item in items
    )
    if tier_a_severe:
        return RiskAction.BLOCK
    if conflicts:
        return RiskAction.WATCH_ONLY
    reliable_negative_domains = {
        item.domain
        for item in items
        if item.event_direction == "NEGATIVE"
        and item.materiality >= 0.7
        and item.source_tier in {"tier_1", "tier_2"}
        and item.url
        and item.provider_verified
    }
    if len(reliable_negative_domains) >= 2:
        return RiskAction.WATCH_ONLY
    if any(item.event_direction == "NEGATIVE" and item.materiality >= 0.6 for item in items):
        return RiskAction.WATCH_ONLY
    if any(item.event_direction == "NEGATIVE" for item in items):
        return RiskAction.DEMOTE
    if any(item.event_direction == "POSITIVE" and item.materiality >= 0.7 for item in items):
        return RiskAction.PROMOTE
    return RiskAction.KEEP


def novelty_score(published_at: datetime | None, decision_as_of_time: datetime, *, half_life_hours: float = 72.0) -> float | None:
    """Time-decay used only for event review; unknown time remains null."""
    if published_at is None:
        return None
    age_hours = max(0.0, (decision_as_of_time - published_at).total_seconds() / 3600)
    return round(0.5 ** (age_hours / half_life_hours), 6)


def reaction_adjusted_opportunity(opportunity: float, price_reaction_percent: float | None) -> float:
    """Large same-direction price moves reduce remaining event opportunity."""
    if price_reaction_percent is None:
        return opportunity
    discount = min(0.8, abs(float(price_reaction_percent)) / 20)
    return round(opportunity * (1 - discount), 4)


def _optional_unit(value: Any) -> float | None:
    if value is None:
        return None
    return _unit_value(value, default=None)


def _unit_value(value: Any, *, default: float | None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return default
    return number


def _is_finite_numeric_input(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0.0 <= number <= 1.0
