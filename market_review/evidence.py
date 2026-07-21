from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from datasource.interfaces.search_provider import SearchProvider
from datasource.search_provider import SearchProviderUnavailable
from market_review.schemas import MarketDailySnapshotData, MarketEvidence, SearchQuery, SearchResult


OFFICIAL_DOMAINS = {
    "gov.cn", "pbc.gov.cn", "csrc.gov.cn", "sse.com.cn", "szse.cn", "bse.cn",
    "stats.gov.cn", "mof.gov.cn", "ndrc.gov.cn",
}
REPUTABLE_MEDIA = {"xinhua.net", "people.com.cn", "cnstock.com", "stcn.com", "yicai.com", "caixin.com"}
TRACKING_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from"}


class MarketSearchQueryGenerator:
    def generate(self, snapshot: MarketDailySnapshotData, provider: str, max_queries: int) -> list[SearchQuery]:
        direction_word = {"UP": "上涨", "DOWN": "下跌", "FLAT": "震荡", "MIXED": "分化"}[snapshot.market_direction]
        candidates = [
            (f"A股 {snapshot.trade_date.isoformat()} {direction_word} 原因", "大盘总体"),
            (f"沪深股市 {snapshot.trade_date.isoformat()} 复盘", "大盘总体"),
            (f"证监会 交易所 央行 {snapshot.trade_date.isoformat()} 公告", "官方政策与宏观"),
        ]
        for sector in snapshot.industries[:2] + snapshot.concepts[:2]:
            candidates.append((f"{sector.sector_name} {snapshot.trade_date.isoformat()} 涨跌 原因", f"板块:{sector.sector_name}"))
        return [
            SearchQuery(query=query, generated_reason=reason, trade_date=snapshot.trade_date, provider=provider)
            for query, reason in candidates[:max_queries]
        ]


class MarketEvidenceNormalizer:
    def __init__(self, *, max_age_hours: int = 36, allow_unknown_publish_time: bool = False) -> None:
        self.max_age_hours = max_age_hours
        self.allow_unknown_publish_time = allow_unknown_publish_time

    def normalize(self, trade_date: date, result: SearchResult, *, decision_time: datetime | None = None, relevance_hint: float = 0.7) -> MarketEvidence | None:
        publish_time = result.publish_time
        if publish_time is None and not self.allow_unknown_publish_time:
            return None
        if publish_time is not None:
            reference = decision_time or datetime.combine(trade_date, datetime.max.time(), tzinfo=timezone.utc)
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            if publish_time.tzinfo is None:
                publish_time = publish_time.replace(tzinfo=timezone.utc)
            if publish_time > reference:
                return None
            age_hours = (reference - publish_time).total_seconds() / 3600
            if age_hours > self.max_age_hours:
                return None
            timeliness = max(0.0, 1 - age_hours / self.max_age_hours)
        else:
            timeliness = 0.35
        canonical = canonical_url(str(result.url))
        domain = (urlparse(canonical).hostname or result.domain).lower()
        tier, official, credibility = source_tier(domain)
        summary = _clean_text(result.snippet)[:500]
        title = _clean_text(result.title)[:512]
        if not title or not summary:
            return None
        relevance = max(0.0, min(1.0, relevance_hint))
        final_score = credibility * 0.45 + relevance * 0.35 + timeliness * 0.20
        content_hash = _sha(f"{title_normalized(title)}|{summary}")
        evidence_id = f"evi-{_sha(f'{trade_date}:{canonical}:{content_hash}')[:24]}"
        status = "VERIFIED_OFFICIAL" if official and final_score >= 0.65 else "SINGLE_SOURCE_PROBABLE" if final_score >= 0.55 else "LOW_CONFIDENCE"
        return MarketEvidence(
            evidence_id=evidence_id,
            trade_date=trade_date,
            title=title,
            source_name=result.source_name,
            domain=domain,
            url=canonical,
            publish_time=publish_time,
            fetched_at=result.fetched_at,
            summary=summary,
            source_tier=tier,
            official=official,
            credibility_score=round(credibility, 4),
            relevance_score=round(relevance, 4),
            timeliness_score=round(timeliness, 4),
            final_evidence_score=round(final_score, 4),
            status=status,
            content_hash=content_hash,
            provider=result.provider,
        )


class MarketEvidenceDeduplicator:
    def deduplicate(self, evidence: list[MarketEvidence], multi_source_count: int = 2) -> tuple[list[MarketEvidence], int]:
        groups: dict[str, list[MarketEvidence]] = defaultdict(list)
        for item in evidence:
            event_key = _sha(title_normalized(item.title))[:20]
            groups[event_key].append(item)
        selected: list[MarketEvidence] = []
        duplicate_count = 0
        for group_id, items in groups.items():
            by_url: dict[str, MarketEvidence] = {}
            for item in sorted(items, key=lambda row: row.final_evidence_score, reverse=True):
                key = canonical_url(str(item.url))
                if key in by_url or any(existing.content_hash == item.content_hash for existing in by_url.values()):
                    duplicate_count += 1
                    continue
                by_url[key] = item
            unique = list(by_url.values())
            domains = {item.domain for item in unique}
            multi_supported = len(domains) >= multi_source_count
            for item in unique:
                selected.append(item.model_copy(update={
                    "duplicate_group_id": group_id,
                    "status": "MULTI_SOURCE_SUPPORTED" if multi_supported and item.status != "VERIFIED_OFFICIAL" else item.status,
                }))
        return sorted(selected, key=lambda row: (-row.final_evidence_score, row.evidence_id)), duplicate_count


class MarketEvidenceSearchService:
    def __init__(self, provider: SearchProvider, config: dict[str, Any]) -> None:
        self.provider = provider
        self.config = config
        self.query_generator = MarketSearchQueryGenerator()
        self.normalizer = MarketEvidenceNormalizer(
            max_age_hours=int(config.get("max_age_hours", 36)),
            allow_unknown_publish_time=bool(config.get("allow_unknown_publish_time", False)),
        )
        self.deduplicator = MarketEvidenceDeduplicator()

    def collect(self, snapshot: MarketDailySnapshotData, *, enabled: bool) -> dict[str, Any]:
        if not enabled:
            return _empty_collection("DATA_ONLY", self.provider.name)
        queries = self.query_generator.generate(snapshot, self.provider.name, int(self.config.get("max_queries", 12)))
        end = snapshot.decision_time
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        start = end - timedelta(hours=int(self.config.get("max_age_hours", 36)))
        raw_results: list[SearchResult] = []
        external_call_occurred = False
        try:
            for query in queries:
                external_call_occurred = True
                results = self.provider.search(
                    query.query, start, end, "zh-CN", int(self.config.get("max_results_per_query", 8)), None,
                )
                query.result_count = len(results)
                query.executed_at = datetime.now(timezone.utc)
                raw_results.extend(results)
        except SearchProviderUnavailable as exc:
            return _empty_collection("UNAVAILABLE", self.provider.name, queries=queries, error_code=str(exc))
        except Exception:
            return _empty_collection("UNAVAILABLE", self.provider.name, queries=queries, error_code="SEARCH_PROVIDER_ERROR")
        normalized = [self.normalizer.normalize(snapshot.trade_date, item, decision_time=snapshot.decision_time) for item in raw_results]
        rejected = sum(item is None for item in normalized)
        valid = [item for item in normalized if item is not None]
        deduplicated, duplicate_count = self.deduplicator.deduplicate(
            valid, int(self.config.get("multi_source_confirmation_count", 2)),
        )
        threshold = float(self.config.get("evidence_min_confidence", 0.55))
        usable = [item for item in deduplicated if item.final_evidence_score >= threshold and item.status not in {"LOW_CONFIDENCE", "REJECTED", "STALE", "DUPLICATE"}]
        usable = usable[: int(self.config.get("max_evidence_items", 30))]
        official_count = sum(item.official for item in usable)
        multi_count = sum(item.status == "MULTI_SOURCE_SUPPORTED" for item in usable)
        status = "VERIFIED" if official_count or multi_count else "PARTIAL" if usable else "UNAVAILABLE"
        return {
            "search_status": status,
            "provider": self.provider.name,
            "provider_version": self.provider.version,
            "queries": [query.model_dump(mode="json") for query in queries],
            "evidence": usable,
            "stats": {
                "query_count": len(queries),
                "raw_result_count": len(raw_results),
                "deduplicated_count": len(deduplicated),
                "valid_evidence_count": len(usable),
                "official_evidence_count": official_count,
                "multi_source_supported_count": multi_count,
                "rejected_count": rejected,
                "duplicate_count": duplicate_count,
            },
            "external_call_occurred": external_call_occurred,
            "error_code": None,
        }


def canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("UNSAFE_EVIDENCE_URL")
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in TRACKING_KEYS))
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))


def title_normalized(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


def source_tier(domain: str) -> tuple[str, bool, float]:
    if any(domain == item or domain.endswith(f".{item}") for item in OFFICIAL_DOMAINS):
        return "TIER_1_OFFICIAL", True, 0.95
    if any(domain == item or domain.endswith(f".{item}") for item in REPUTABLE_MEDIA):
        return "TIER_3_REPUTABLE_MEDIA", False, 0.8
    return "TIER_4_SECONDARY_COMMENTARY", False, 0.6


def _empty_collection(status: str, provider: str, *, queries: list[SearchQuery] | None = None, error_code: str | None = None) -> dict[str, Any]:
    return {
        "search_status": status,
        "provider": provider,
        "provider_version": "unknown",
        "queries": [query.model_dump(mode="json") for query in (queries or [])],
        "evidence": [],
        "stats": {
            "query_count": len(queries or []), "raw_result_count": 0, "deduplicated_count": 0,
            "valid_evidence_count": 0, "official_evidence_count": 0,
            "multi_source_supported_count": 0, "rejected_count": 0, "duplicate_count": 0,
        },
        "external_call_occurred": False,
        "error_code": error_code,
    }


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
