from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from database.models.system import SystemConfig
from database.models.workbench import WorkbenchRunRegistry
from datasource.search_provider import build_market_search_provider
from market_review.evidence import MarketEvidenceSearchService
from market_review.repository import MarketReviewRepository
from market_review.rules import MarketOutlookRuleEngine, MarketRegimeEngine
from market_review.schemas import MarketEvidence
from market_review.snapshot import MarketDailySnapshotService, load_market_review_config
from market_review.wire import DataOnlyReviewBuilder, GatewayMarketReviewPro, validate_wire_facts


class MarketReviewService:
    def __init__(self, session, *, config: dict[str, Any] | None = None, cache_root=None, search_provider=None) -> None:
        self.session = session
        self.config = config or load_market_review_config()
        if config is None:
            self.config = _merge_database_settings(session, self.config)
        self.repository = MarketReviewRepository(session)
        self.snapshot_service = MarketDailySnapshotService(session, cache_root=cache_root, config=self.config)
        search_config = dict(self.config.get("search") or {})
        self._search_provider_supplied = search_provider is not None
        self.search_provider = search_provider or build_market_search_provider(
            str(search_config.get("provider", "AUTO")),
            allow_external_calls=bool(search_config.get("allow_external_calls", False)),
        )
        self.evidence_service = MarketEvidenceSearchService(self.search_provider, search_config)

    def run(
        self,
        trade_date: date,
        *,
        mode: str = "DATA_ONLY",
        force: bool = False,
        decision_time: datetime | None = None,
        allow_real_pro: bool = False,
        allow_real_search: bool = False,
    ) -> dict[str, Any]:
        normalized_mode = mode.upper()
        if normalized_mode not in {"FULL", "DATA_ONLY", "REFRESH_EVIDENCE", "REGENERATE_SUMMARY"}:
            raise ValueError("INVALID_MARKET_REVIEW_MODE")
        decision_time = decision_time or datetime.combine(
            trade_date, time(15, 30), tzinfo=ZoneInfo("Asia/Shanghai")
        )
        if allow_real_search and not self._search_provider_supplied:
            search_config = dict(self.config.get("search") or {})
            self.search_provider = build_market_search_provider(
                str(search_config.get("provider", "AUTO")), allow_external_calls=True,
            )
            self.evidence_service = MarketEvidenceSearchService(self.search_provider, search_config)
        snapshot = self.snapshot_service.build(trade_date, decision_time)
        regime = MarketRegimeEngine(str(self.config.get("regime_version", "market_regime_v1"))).evaluate(snapshot)

        if normalized_mode == "REGENERATE_SUMMARY":
            latest = self.repository.latest_run(trade_date)
            search = self._search_from_latest(latest) if latest else self.evidence_service.collect(snapshot, enabled=False)
        else:
            search_enabled = normalized_mode in {"FULL", "REFRESH_EVIDENCE"} and bool((self.config.get("search") or {}).get("enabled", True))
            search = self.evidence_service.collect(snapshot, enabled=search_enabled)
        evidence: list[MarketEvidence] = list(search.get("evidence") or [])
        evidence_score = sum(item.final_evidence_score for item in evidence) / len(evidence) * 100 if evidence else 50.0
        outlook = MarketOutlookRuleEngine(str(self.config.get("outlook_rule_version", "market_outlook_rule_v1"))).evaluate(
            snapshot, regime, external_evidence_score=evidence_score,
        )
        evidence_hash = _hash([item.model_dump(mode="json") for item in evidence])
        review_input_hash = _hash({
            "trade_date": trade_date.isoformat(),
            "decision_time": decision_time.isoformat(),
            "snapshot_hash": snapshot.snapshot_hash,
            "dataset_watermark_hash": snapshot.dataset_watermark_hash,
            "market_regime_version": regime.version,
            "outlook_rule_version": outlook.probability_model_version,
            "evidence_set_hash": evidence_hash,
            "search_provider": search.get("provider"),
            "search_provider_version": search.get("provider_version"),
            "search_status": search.get("search_status"),
            "prompt_version": self.config.get("prompt_version"),
            "contract_version": self.config.get("contract_version"),
            "actual_model": (self.config.get("pro") or {}).get("actual_model"),
            "model_config": self.config.get("pro"),
        })
        if not force and normalized_mode not in {"REFRESH_EVIDENCE", "REGENERATE_SUMMARY"}:
            cached = self.repository.compatible_run(trade_date, review_input_hash)
            if cached:
                payload = self.repository.bundle(cached)
                payload["run"]["cache_status"] = "SUCCESS_CACHE_HIT"
                return payload

        wire, token_usage_id = self._build_review(snapshot, regime, outlook, search, allow_real_pro)
        wire = validate_wire_facts(wire.model_dump(mode="json") if hasattr(wire, "model_dump") else wire, snapshot, regime, outlook, evidence)
        snapshot_row = self.repository.save_snapshot(snapshot, regime)
        self.repository.upsert_evidence(evidence)
        pipeline_run_id = self._pipeline_run_id(trade_date)
        status = "DATA_ONLY" if wire.search_status == "DATA_ONLY" else "PARTIAL_SUCCESS" if wire.search_status in {"PARTIAL", "UNAVAILABLE"} else "SUCCESS"
        row = self.repository.save_run(
            run_id=f"market-review-{uuid.uuid4().hex[:20]}",
            status=status,
            snapshot_row=snapshot_row,
            search=search,
            review_input_hash=review_input_hash,
            evidence_hash=evidence_hash,
            config=self.config,
            regime=regime,
            outlook=outlook,
            wire=wire,
            token_usage_id=token_usage_id,
            pipeline_run_id=pipeline_run_id,
        )
        return self.repository.bundle(row)

    def latest(self, trade_date: date | None = None, run_id: str | None = None) -> dict[str, Any] | None:
        row = self.repository.latest_run(trade_date, run_id)
        return self.repository.bundle(row) if row else None

    def history(self, trade_date: date | None = None) -> list[dict[str, Any]]:
        return self.repository.history(trade_date)

    def _build_review(self, snapshot, regime, outlook, search, allow_real_pro):
        evidence = list(search.get("evidence") or [])
        pro_config = dict(self.config.get("pro") or {})
        if bool(pro_config.get("enabled")) and allow_real_pro:
            payload, usage_id = GatewayMarketReviewPro(self.session, pro_config).generate({
                "trade_date": snapshot.trade_date.isoformat(),
                "snapshot": snapshot.model_dump(mode="json"),
                "regime": regime.model_dump(mode="json"),
                "outlook": outlook.model_dump(mode="json"),
                "evidence": [item.model_dump(mode="json") for item in evidence],
                "search_status": search.get("search_status"),
            }, allow_real_pro=True)
            return payload, usage_id
        return DataOnlyReviewBuilder().build(
            snapshot, regime, outlook,
            search_status=str(search.get("search_status") or "DATA_ONLY"),
            evidence=evidence,
        ), None

    def _search_from_latest(self, latest) -> dict[str, Any]:
        if latest is None:
            return self.evidence_service.collect(None, enabled=False)
        bundle = self.repository.bundle(latest)
        evidence = [MarketEvidence.model_validate(item) for item in bundle.get("evidence") or []]
        search = dict(bundle.get("search") or {})
        search["evidence"] = evidence
        return search

    def _pipeline_run_id(self, trade_date: date) -> str | None:
        row = self.session.scalar(
            select(WorkbenchRunRegistry)
            .where(WorkbenchRunRegistry.trade_date == trade_date)
            .order_by(WorkbenchRunRegistry.reconciled_at.desc())
        )
        return row.pipeline_run_id if row else None


def _hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _merge_database_settings(session, config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    search = dict(merged.get("search") or {})
    pro = dict(merged.get("pro") or {})
    rows = list(session.scalars(select(SystemConfig).where(SystemConfig.config_key.like("workbench.market_review_%"))))
    values = {row.config_key.removeprefix("workbench."): row.config_value for row in rows}
    direct = {
        "market_review_enabled": "enabled",
        "market_review_auto_run": "auto_run_after_pipeline",
        "market_review_include_outlook": "include_tomorrow_outlook",
        "market_review_auto_update_excel": "auto_update_excel",
        "market_review_allow_data_only_fallback": "allow_data_only_fallback",
        "market_review_evidence_min_confidence": "evidence_min_confidence",
    }
    for source, target in direct.items():
        if source in values:
            merged[target] = values[source]
    search_map = {
        "market_review_search_enabled": "enabled",
        "market_review_search_provider": "provider",
        "market_review_max_queries": "max_queries",
        "market_review_max_results_per_query": "max_results_per_query",
        "market_review_max_age_hours": "max_age_hours",
        "market_review_official_source_priority": "official_source_priority",
        "market_review_multi_source_count": "multi_source_confirmation_count",
        "market_review_evidence_min_confidence": "evidence_min_confidence",
    }
    for source, target in search_map.items():
        if source in values:
            search[target] = values[source]
    pro_map = {
        "market_review_pro_enabled": "enabled",
        "market_review_pro_model_alias": "model_alias",
        "market_review_pro_max_tokens": "max_tokens",
    }
    for source, target in pro_map.items():
        if source in values:
            pro[target] = values[source]
    merged["search"] = search
    merged["pro"] = pro
    return merged
