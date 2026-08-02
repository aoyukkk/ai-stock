from __future__ import annotations

from datetime import datetime, timezone

from fundamentals.cache import ReportPeriodCache
from fundamentals.concepts import TushareConceptIndex
from fundamentals.profile import TushareFundamentalProfile, TushareFundamentalProfileBuilder
from fundamentals.revision import parse_available_at, select_latest_revisions
from fundamentals.period_planner import FinancialPeriodPlanner
from stock_codes import normalize_ts_code
from temporal.freshness import require_decision_as_of_time


class CachedTushareProfileService:
    def __init__(self, cache: ReportPeriodCache | None = None) -> None:
        self.cache = cache or ReportPeriodCache()
        self.concepts = TushareConceptIndex(self.cache.root.parent)

    def latest_cached_period(self) -> str | None:
        periods = set()
        for interface in ("income", "balancesheet", "cashflow", "fina_indicator"):
            root = self.cache.root / interface
            if root.exists():
                periods.update(path.name for path in root.iterdir() if path.is_dir())
        return max(periods) if periods else None

    def build(self, stock_code: str, period: str | None = None, decision_time: datetime | None = None) -> TushareFundamentalProfile:
        lookup_code = normalize_ts_code(stock_code)
        decision_time = require_decision_as_of_time(decision_time)
        periods = [period] if period else self._cached_periods()
        if not periods:
            return TushareFundamentalProfileBuilder().build(
                stock_code,
                as_of_time=decision_time,
                freshness={
                    "freshness_status": "MISSING",
                    "point_in_time_safe": True,
                    "degradation_reason": "NO_CACHED_FUNDAMENTAL_PERIODS",
                },
            )
        cache_evidence: list[dict] = []
        future_record_count = 0

        def cached(interface: str, candidate_period: str, params: dict) -> dict | None:
            payload, evidence = self.cache.read_with_freshness(
                interface,
                candidate_period,
                params,
                now=decision_time,
                allow_stale=True,
            )
            cache_evidence.append(evidence)
            return payload

        identity_period = self._latest_identity_period() or max(periods)
        stock_payload = cached("stock_basic", identity_period, {"period": identity_period})
        stock_basic = next(
            (row for row in (stock_payload or {}).get("records", []) if row.get("ts_code") == lookup_code),
            {},
        )
        companies = []
        for exchange in ("SSE", "SZSE", "BSE"):
            payload = cached("stock_company", identity_period, {"period": identity_period, "exchange": exchange})
            companies.extend((payload or {}).get("records", []))
        company = next((row for row in companies if row.get("ts_code") == lookup_code), {})
        rows, selections = {}, {}
        for interface in ("income", "balancesheet", "cashflow", "fina_indicator"):
            records_by_period = {}
            for candidate_period in periods:
                payload = cached(interface, candidate_period, {"period": candidate_period})
                if payload:
                    candidate_records = list(payload.get("records", []))
                    future_record_count += sum(
                        1
                        for row in candidate_records
                        if row.get("ts_code") == lookup_code
                        and (available := parse_available_at(row)) is not None
                        and available > decision_time
                    )
                    records_by_period[candidate_period] = candidate_records
            rows[interface], selections[interface] = FinancialPeriodPlanner().select_latest(records_by_period, lookup_code, decision_time)
        business = []
        for business_type in ("P", "I", "D"):
            for candidate_period in periods:
                payload = cached("mainbz", candidate_period, {"period": candidate_period, "type": business_type})
                future_record_count += sum(
                    1
                    for row in list((payload or {}).get("records", []))
                    if row.get("ts_code") == lookup_code
                    and (available := parse_available_at(row)) is not None
                    and available > decision_time
                )
                safe_business = select_latest_revisions(
                    list((payload or {}).get("records", [])),
                    decision_time,
                    interface="formal",
                )
                for original in safe_business:
                    if original.get("ts_code") == lookup_code:
                        business.append({**original, "business_type": business_type})
        selection = max(
            (item for item in selections.values() if item.get("latest_financial_period")),
            key=lambda item: item.get("latest_financial_period", ""),
            default={},
        )
        concept = self.concepts.lookup(lookup_code)
        stale = any(item.get("freshness_status") == "STALE" for item in cache_evidence)
        fetched_values = [
            datetime.fromisoformat(item["cache_fetched_at"])
            for item in cache_evidence
            if item.get("cache_fetched_at")
        ]
        point_in_time_safe = True
        cache_fetched_at = max(fetched_values, default=None)
        cache_age_hours = max(
            (float(item["cache_age_hours"]) for item in cache_evidence if item.get("cache_age_hours") is not None),
            default=None,
        )
        return TushareFundamentalProfileBuilder().build(
            stock_code,
            stock_basic=stock_basic,
            company=company,
            income=rows["income"],
            balance=rows["balancesheet"],
            cashflow=rows["cashflow"],
            indicator=rows["fina_indicator"],
            main_business=business,
            concept_tags=concept.normalized_tags,
            concept_mapping_audit={
                "raw_source_concept_tag_count": concept.raw_source_count,
                "normalized_concept_tag_count": concept.normalized_source_count,
                "inferred_concept_tag_count": concept.inferred_count,
                "concept_source_status": concept.source_status,
                "point_in_time_status": "UNVERIFIED_CURRENT_MEMBERSHIP",
            },
            as_of_time=decision_time,
            financial_selection=selection,
            freshness={
                "cache_fetched_at": cache_fetched_at,
                "cache_age_hours": cache_age_hours,
                "freshness_status": "STALE" if stale else "FRESH",
                "point_in_time_safe": point_in_time_safe,
                "degradation_reason": (
                    "FUNDAMENTAL_CACHE_EXCEEDS_TTL"
                    if stale
                    else None
                ),
                "future_record_count": future_record_count,
            },
        )

    def _cached_periods(self) -> list[str]:
        periods = set()
        for interface in ("income", "balancesheet", "cashflow", "fina_indicator"):
            root = self.cache.root / interface
            if root.exists():
                periods.update(path.name for path in root.iterdir() if path.is_dir() and path.name.isdigit())
        return sorted(periods, reverse=True)

    def _latest_identity_period(self) -> str | None:
        root = self.cache.root / "stock_basic"
        periods = [path.name for path in root.iterdir() if path.is_dir() and path.name.isdigit()] if root.exists() else []
        return max(periods) if periods else None
