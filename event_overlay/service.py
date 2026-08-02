from __future__ import annotations

import ast
import csv
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.event_overlay import (
    EventEvidenceItemRecord,
    EventEvidenceSnapshotRecord,
    EventOverlayDataIssueRecord,
    EventReviewResultRecord,
    EventScreeningItemRecord,
    EventScreeningRunRecord,
)
from event_overlay.checkpoint import (
    CHECKPOINT_CONTRACT_VERSION,
    CheckpointStore,
    EventOverlayCheckpointContract,
    exclusive_file_lock,
)
from event_overlay.config import ROOT, load_event_overlay_config
from event_overlay.constants import (
    DECISION_VERSION,
    EVENT_REVIEW_VERSION,
    SCREENING_VERSION,
    SEARCH_CONTRACT_VERSION,
)
from event_overlay.evidence import normalize_search_result
from event_overlay.exporter import export_run
from event_overlay.forward_ab import build_forward_ab_manifest, promote_demote_counterfactual
from event_overlay.hashing import canonical_hash, file_hash
from event_overlay.provider import (
    DeepSeekFlashDirectSearchProvider,
    DirectSearchTimeoutError,
    EventSearchProvider,
    MockEventSearchProvider,
    NoNetworkEventSearchProvider,
)
from event_overlay.schemas import EventEvidenceSnapshot, ScreeningRunManifest
from event_overlay.scoring import build_screening_item, rank_items


SHANGHAI = ZoneInfo("Asia/Shanghai")
RISK_VERSION = "RISK_V2_1_SHADOW"
REAL_SEARCH_CANARY_MAX = 5
FULL_SEARCH_SIZE = 100
PREOPEN_CUTOFF = time(9, 25)


@dataclass(frozen=True)
class RunOptions:
    trade_date: date
    decision_as_of_time: datetime
    base_quant_run_id: str | None = None
    enable_real_search: bool = False
    mock_search: bool = False
    no_network: bool = False
    stock_limit: int | None = None
    skip_pro: bool = True
    resume: bool = False
    confirm_full_search: bool = False


class EventOverlayShadowService:
    def __init__(self, session: Session, *, root: Path = ROOT) -> None:
        self.session = session
        self.root = root
        self.config = load_event_overlay_config(root / "config" / "event_overlay_v3_1.yaml")
        self.prompt_path = root / "prompts" / "event_overlay_v3_1_flash.yaml"
        self.prompt = yaml.safe_load(self.prompt_path.read_text(encoding="utf-8"))

    def run(
        self,
        options: RunOptions,
        *,
        provider: EventSearchProvider | None = None,
    ) -> dict[str, Any]:
        run_lock = (
            self.root
            / "outputs"
            / "event_overlay"
            / options.trade_date.isoformat()
            / ".event_overlay_run.lock"
        )
        with exclusive_file_lock(
            run_lock,
            timeout_seconds=2,
            stale_after_seconds=21_600,
        ):
            return self._run_locked(options, provider=provider)

    def _run_locked(
        self,
        options: RunOptions,
        *,
        provider: EventSearchProvider | None = None,
    ) -> dict[str, Any]:
        self._validate_options(options)
        source_path, source = self._load_source(options.trade_date, options.base_quant_run_id)
        source_file_hash_before = file_hash(source_path)
        universe_path, top100 = self._load_eligible_universe(
            source_path,
            source,
            options.trade_date,
        )
        universe_file_hash_before = file_hash(universe_path)
        prompt_hash = file_hash(self.prompt_path)
        self._validate_preopen_contract(options, source)
        if options.enable_real_search and self._effective_stock_limit(options) == FULL_SEARCH_SIZE:
            self._assert_successful_canary(
                options,
                source=source,
                source_file_hash=source_file_hash_before,
                universe_file_hash=universe_file_hash_before,
                prompt_hash=prompt_hash,
            )
        if options.stock_limit is not None:
            top100 = top100[: options.stock_limit]
        source_input_hash = canonical_hash(top100)
        universe_snapshot_id = f"universe-{source_input_hash[:24]}"
        run_id = f"event-v3-{options.trade_date:%Y%m%d}-{datetime.now(SHANGHAI):%H%M%S}-{uuid4().hex[:8]}"
        output_root = self.root / "outputs" / "event_overlay" / options.trade_date.isoformat()
        output_dir = output_root / run_id
        checkpoint = CheckpointStore(output_root / ".event_overlay_checkpoint.json")
        provider = provider or self._provider(options)
        execution_mode = (
            "MOCK_SEARCH" if options.mock_search
            else "REAL_DIRECT_SEARCH" if options.enable_real_search
            else "NO_NETWORK"
        )
        snapshots: list[dict[str, Any]] = []
        screening_items = []
        checkpoint_audit: list[dict[str, Any]] = []
        data_quality: list[dict[str, Any]] = []
        actual_network_calls = 0
        web_search_request_count = 0
        reused = 0
        stale = 0
        successful_evaluations = 0
        prefetched: dict[str, dict[str, Any]] = {}

        if options.enable_real_search and len(top100) == FULL_SEARCH_SIZE:
            pending: list[tuple[dict[str, Any], str, EventOverlayCheckpointContract, dict[str, Any]]] = []
            for stock in top100:
                code, _, _, contract = self._checkpoint_context(
                    stock,
                    options=options,
                    source=source,
                    prompt_hash=prompt_hash,
                )
                audit = checkpoint.audit(contract)
                if not audit["reuse_allowed"]:
                    pending.append((stock, code, contract, audit))
            workers = min(
                max(1, int((self.config.get("web_search") or {}).get("workers") or 4)),
                len(pending) or 1,
            )
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        provider.search,
                        stock,
                        decision_as_of_time=options.decision_as_of_time,
                        max_sources=int(self.config["screening"]["max_sources_per_stock"]),
                    ): (stock, code, contract, audit)
                    for stock, code, contract, audit in pending
                }
                for future in as_completed(futures):
                    stock, code, contract, audit = futures[future]
                    network_calls = 0
                    web_requests = 0
                    issue = None
                    try:
                        raw_search = future.result()
                        network_calls = int(raw_search.get("network_calls") or 0)
                        usage = raw_search.get("usage") or {}
                        if isinstance(usage, dict):
                            web_requests = int(usage.get("web_search_requests") or 0)
                        review = normalize_search_result(
                            stock,
                            raw_search,
                            decision_as_of_time=options.decision_as_of_time,
                            max_events=int(self.config["screening"]["max_events_per_stock"]),
                            confidence_discount=float(self.config["screening"]["confidence_discount_direct_search"]),
                            freshness_policy=self.config["freshness"],
                            source_tier_policy=self.config["source_tiers"],
                        )
                    except (TimeoutError, DirectSearchTimeoutError) as exc:
                        network_calls += int(
                            getattr(exc, "network_calls", 0) or 0
                        )
                        web_requests += int(
                            getattr(exc, "web_search_requests", 0) or 0
                        )
                        review = normalize_search_result(
                            stock,
                            {"status": "SEARCH_TIMEOUT", "provider": provider.name, "items": []},
                            decision_as_of_time=options.decision_as_of_time,
                            max_events=int(self.config["screening"]["max_events_per_stock"]),
                            confidence_discount=1.0,
                            freshness_policy=self.config["freshness"],
                            source_tier_policy=self.config["source_tiers"],
                        )
                        issue = _issue(code, "SEARCH_TIMEOUT", "WARN", "事件搜索超时")
                    except Exception as exc:
                        network_calls += int(
                            getattr(exc, "network_calls", 0) or 0
                        )
                        web_requests += int(
                            getattr(exc, "web_search_requests", 0) or 0
                        )
                        review = normalize_search_result(
                            stock,
                            {"status": "SEARCH_FAILED", "provider": provider.name, "items": []},
                            decision_as_of_time=options.decision_as_of_time,
                            max_events=int(self.config["screening"]["max_events_per_stock"]),
                            confidence_discount=1.0,
                            freshness_policy=self.config["freshness"],
                            source_tier_policy=self.config["source_tiers"],
                        )
                        detail = type(exc).__name__
                        if isinstance(exc, RuntimeError) and str(exc).startswith("DIRECT_SEARCH_"):
                            detail = str(exc)[:200]
                        issue = _issue(code, "SEARCH_FAILED", "WARN", detail)
                    raw_review = review.model_dump(mode="json")
                    checkpoint.save(contract, raw_review)
                    prefetched[code] = {
                        "review": raw_review,
                        "audit": audit,
                        "network_calls": network_calls,
                        "web_search_requests": web_requests,
                        "issue": issue,
                    }

        for stock in top100:
            code, query, stock_input_hash, contract = self._checkpoint_context(
                stock,
                options=options,
                source=source,
                prompt_hash=prompt_hash,
            )
            prefetched_item = prefetched.get(code)
            audit = (
                prefetched_item["audit"]
                if prefetched_item is not None
                else checkpoint.audit(contract)
            )
            checkpoint_status = (
                "NEW" if prefetched_item is not None
                else "REUSED" if audit["reuse_allowed"] else "NEW"
            )
            if prefetched_item is not None:
                actual_network_calls += int(prefetched_item["network_calls"])
                web_search_request_count += int(prefetched_item["web_search_requests"])
                if prefetched_item["issue"] is not None:
                    data_quality.append(prefetched_item["issue"])
                from event_overlay.schemas import EventReviewResult
                review = EventReviewResult.model_validate(prefetched_item["review"])
                raw_review = prefetched_item["review"]
                if audit["stale"]:
                    stale += 1
            elif audit["reuse_allowed"]:
                raw_review = audit["saved"]["result"]
                reused += 1
                from event_overlay.schemas import EventReviewResult
                review = EventReviewResult.model_validate(raw_review)
            else:
                if audit["stale"]:
                    stale += 1
                try:
                    raw_search = provider.search(
                        stock,
                        decision_as_of_time=options.decision_as_of_time,
                        max_sources=int(self.config["screening"]["max_sources_per_stock"]),
                    )
                    actual_network_calls += int(raw_search.get("network_calls") or 0)
                    usage = raw_search.get("usage") or {}
                    if isinstance(usage, dict):
                        web_search_request_count += int(
                            usage.get("web_search_requests") or 0
                        )
                    review = normalize_search_result(
                        stock,
                        raw_search,
                        decision_as_of_time=options.decision_as_of_time,
                        max_events=int(self.config["screening"]["max_events_per_stock"]),
                        confidence_discount=float(self.config["screening"]["confidence_discount_direct_search"]),
                        freshness_policy=self.config["freshness"],
                        source_tier_policy=self.config["source_tiers"],
                    )
                except (TimeoutError, DirectSearchTimeoutError) as exc:
                    actual_network_calls += int(
                        getattr(exc, "network_calls", 0) or 0
                    )
                    web_search_request_count += int(
                        getattr(exc, "web_search_requests", 0) or 0
                    )
                    review = normalize_search_result(
                        stock,
                        {"status": "SEARCH_TIMEOUT", "provider": provider.name, "items": []},
                        decision_as_of_time=options.decision_as_of_time,
                        max_events=int(self.config["screening"]["max_events_per_stock"]),
                        confidence_discount=1.0,
                        freshness_policy=self.config["freshness"],
                        source_tier_policy=self.config["source_tiers"],
                    )
                    data_quality.append(_issue(code, "SEARCH_TIMEOUT", "WARN", "事件搜索超时"))
                except Exception as exc:
                    actual_network_calls += int(
                        getattr(exc, "network_calls", 0) or 0
                    )
                    web_search_request_count += int(
                        getattr(exc, "web_search_requests", 0) or 0
                    )
                    review = normalize_search_result(
                        stock,
                        {"status": "SEARCH_FAILED", "provider": provider.name, "items": []},
                        decision_as_of_time=options.decision_as_of_time,
                        max_events=int(self.config["screening"]["max_events_per_stock"]),
                        confidence_discount=1.0,
                        freshness_policy=self.config["freshness"],
                        source_tier_policy=self.config["source_tiers"],
                    )
                    detail = type(exc).__name__
                    if isinstance(exc, RuntimeError) and str(exc).startswith("DIRECT_SEARCH_"):
                        detail = str(exc)[:200]
                    data_quality.append(_issue(code, "SEARCH_FAILED", "WARN", detail))
                raw_review = review.model_dump(mode="json")
                checkpoint.save(contract, raw_review)
            if (
                review.resolved_stock_name
                and not _looks_mojibake(review.resolved_stock_name)
            ):
                stock["stock_name"] = review.resolved_stock_name
            if review.search_status not in {"SEARCH_FAILED", "SEARCH_TIMEOUT"}:
                successful_evaluations += 1
            snapshot_payload = {
                "run_id": run_id,
                "trade_date": options.trade_date,
                "decision_as_of_time": options.decision_as_of_time,
                "stock_code": code,
                "query": query,
                "search_status": review.search_status,
                "provider": (
                    review.material_events[0].provider
                    if review.material_events else provider.name
                ),
                "provider_verified": review.provider_verified,
                "direct_search_used": review.direct_search_used,
                "confidence_discount_applied": review.confidence_discount_applied,
                "production_eligible": False,
                "shadow_eligible": True,
                "items": [item.model_dump(mode="json") for item in review.material_events],
                "review_conflicts": review.conflicts,
                "review_warnings": review.warnings,
                "input_hash": stock_input_hash,
                "contract_version": SEARCH_CONTRACT_VERSION,
            }
            snapshot_content_hash = canonical_hash(snapshot_payload)
            snapshot = EventEvidenceSnapshot(
                snapshot_id=f"event-snapshot-{run_id[-8:]}-{code}",
                content_hash=snapshot_content_hash,
                **snapshot_payload,
            )
            snapshots.append(snapshot.model_dump(mode="json"))
            screening_items.append(build_screening_item(
                stock,
                review,
                snapshot_id=snapshot.snapshot_id,
                checkpoint_status=checkpoint_status,
                scoring_policy=self.config["scoring"],
            ))
            checkpoint_audit.append({
                "股票代码": code,
                "复用允许": audit["reuse_allowed"],
                "状态": checkpoint_status,
                "输入Hash一致": audit["reused_input_hash_match"],
                "过期Checkpoint": audit["stale"],
                "原因": audit["reasons"],
                "Checkpoint合同Hash": contract.checkpoint_hash,
            })
            for warning in review.warnings:
                data_quality.append(_issue(code, warning, "WARN", warning))

        ranked = rank_items(screening_items, int(self.config["screening"]["output_top_n"]))
        self._assert_ranked_hard_gate_integrity(ranked)
        ranked_payload = [item.model_dump(mode="json") for item in ranked]
        v2_codes = self._v2_top20_codes(options.trade_date, top100)
        v3_codes = [row["stock_code"] for row in ranked_payload if row["selected_top20"]]
        comparison = self._comparison(ranked_payload, v2_codes, snapshots)
        forward_ab = build_forward_ab_manifest(v2_codes, v3_codes)
        manifest_material = {
            "run_id": run_id,
            "trade_date": options.trade_date,
            "decision_as_of_time": options.decision_as_of_time,
            "source_run_id": str(source["run_id"]),
            "source_input_hash": source_input_hash,
            "universe_snapshot_id": universe_snapshot_id,
            "factor_version": str(source["factor_version"]),
            "screening_version": SCREENING_VERSION,
            "decision_version": DECISION_VERSION,
            "event_review_version": EVENT_REVIEW_VERSION,
            "search_contract_version": SEARCH_CONTRACT_VERSION,
            "production_or_shadow": "SHADOW",
            "execution_mode": execution_mode,
            "real_search_enabled": options.enable_real_search,
            "historical_replay": (
                options.decision_as_of_time.astimezone(SHANGHAI).date()
                != datetime.now(SHANGHAI).date()
            ),
            "target_trade_date": source.get("monday_trade_date"),
            "input_count": len(top100),
            "output_count": len(v3_codes),
            "actual_network_calls": actual_network_calls,
            "web_search_request_count": web_search_request_count,
            "logical_evaluations": len(top100),
            "successful_evaluation_count": successful_evaluations,
            "reused_checkpoint_count": reused,
            "stale_checkpoint_count": stale,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }
        manifest = ScreeningRunManifest(
            **manifest_material,
            content_hash=canonical_hash(manifest_material),
        ).model_dump(mode="json")
        search_failure_count = sum(
            1 for issue in data_quality if issue.get("issue_code") == "SEARCH_FAILED"
        )
        provider_failure_count = sum(
            1
            for issue in data_quality
            if issue.get("issue_code") in {"SEARCH_FAILED", "SEARCH_TIMEOUT"}
        )
        final_status = (
            "V3_REAL_SEARCH_CANARY_FAILED"
            if options.enable_real_search
            and (
                provider_failure_count > 0
                or successful_evaluations != len(top100)
                or actual_network_calls + reused < len(top100)
            )
            else "V3_EVENT_OVERLAY_SHADOW_READY"
        )
        manifest["prompt_version"] = self.prompt["prompt_version"]
        manifest["prompt_hash"] = prompt_hash
        manifest["checkpoint_contract_version"] = CHECKPOINT_CONTRACT_VERSION
        manifest["risk_version"] = RISK_VERSION
        evidence_items = [
            event
            for snapshot in snapshots
            for event in (snapshot.get("items") or [])
        ]
        score_eligible_count = sum(
            1 for event in evidence_items if bool(event.get("score_eligible"))
        )
        stocks_with_eligible_evidence = sum(
            1
            for snapshot in snapshots
            if any(
                bool(event.get("score_eligible"))
                for event in (snapshot.get("items") or [])
            )
        )
        manifest["v3_1_policy"] = {
            "search_granularity": "ONE_STRUCTURED_SEARCH_PER_STOCK",
            "quant_factor_rescoring_by_llm": False,
            "no_eligible_evidence_effect": "QUANT_SCORE_UNCHANGED",
            "scoring": self.config["scoring"],
            "freshness": self.config["freshness"],
            "source_tier_policy_hash": canonical_hash(self.config["source_tiers"]),
        }
        manifest["evidence_eligibility"] = {
            "event_count": len(evidence_items),
            "score_eligible_count": score_eligible_count,
            "score_ineligible_count": len(evidence_items) - score_eligible_count,
            "stocks_with_eligible_evidence": stocks_with_eligible_evidence,
            "stocks_without_eligible_evidence": len(snapshots) - stocks_with_eligible_evidence,
            "source_tier_override_count": sum(
                1
                for event in evidence_items
                if (event.get("raw_metadata") or {}).get("reported_source_tier")
                and (event.get("raw_metadata") or {}).get("reported_source_tier")
                != event.get("source_tier")
            ),
        }
        manifest["hard_gate_integrity"] = {
            "selection_policy": "RANKED_HARD_GATE_FREE_TOP100",
            "input_hard_gate_count": sum(
                1
                for row in top100
                if _parse_hard_gate_flag(row.get("hard_gate", False))
                or bool(_parse_hard_gate_reasons(row.get("hard_gate_reasons")))
            ),
            "selected_top20_hard_gate_count": 0,
        }
        manifest["source_artifact"] = {
            "path": str(source_path),
            "sha256": source_file_hash_before,
        }
        manifest["universe_source_artifact"] = {
            "path": str(universe_path),
            "sha256": universe_file_hash_before,
            "selection_policy": "RANKED_HARD_GATE_FREE_TOP100",
        }
        manifest["frozen_decision_verification"] = self._frozen_decision_hashes()
        manifest["forward_ab"] = forward_ab
        manifest["counterfactual"] = promote_demote_counterfactual(v2_codes, v3_codes)
        manifest["search_failure_count"] = search_failure_count
        manifest["provider_failure_count"] = provider_failure_count
        manifest["final_status"] = final_status
        database_publish_eligible = (
            final_status == "V3_EVENT_OVERLAY_SHADOW_READY"
            and options.enable_real_search
            and len(top100) == FULL_SEARCH_SIZE
        )
        manifest["database_publish_eligible"] = database_publish_eligible
        market_regime = self._market_regime(options.trade_date)
        manifest["market_regime"] = market_regime
        manifest["content_hash"] = canonical_hash({
            key: value for key, value in manifest.items() if key != "content_hash"
        })
        if file_hash(source_path) != source_file_hash_before:
            raise RuntimeError("BASE_V2_SOURCE_CHANGED_DURING_EVENT_OVERLAY_RUN")
        if file_hash(universe_path) != universe_file_hash_before:
            raise RuntimeError("BASE_V2_FULL_UNIVERSE_CHANGED_DURING_EVENT_OVERLAY_RUN")
        artifacts = export_run(
            output_dir,
            trade_date=options.trade_date.isoformat(),
            run_id=run_id,
            manifest=manifest,
            items=ranked_payload,
            snapshots=snapshots,
            checkpoint_audit=checkpoint_audit,
            data_quality=data_quality,
            comparison=comparison,
            pro_results=[],
            market_regime=market_regime,
            theme_concentration=[],
        )
        if file_hash(source_path) != source_file_hash_before:
            raise RuntimeError("BASE_V2_SOURCE_CHANGED_DURING_EVENT_OVERLAY_RUN")
        if file_hash(universe_path) != universe_file_hash_before:
            raise RuntimeError("BASE_V2_FULL_UNIVERSE_CHANGED_DURING_EVENT_OVERLAY_RUN")
        # Failed canaries remain immutable filesystem audit artifacts, but must
        # never become the latest Web/API business run.
        if database_publish_eligible:
            self._persist(manifest, ranked_payload, snapshots, data_quality)
        return {
            "status": final_status,
            "run_id": run_id,
            "trade_date": options.trade_date.isoformat(),
            "source_run_id": source["run_id"],
            "factor_version": source["factor_version"],
            "input_count": len(top100),
            "top20_count": len(v3_codes),
            "actual_network_calls": actual_network_calls,
            "search_failure_count": search_failure_count,
            "provider_failure_count": provider_failure_count,
            "checkpoint_reused": reused,
            "stale_checkpoints": stale,
            "artifacts": artifacts,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }

    def _validate_options(self, options: RunOptions) -> None:
        if options.decision_as_of_time.tzinfo is None:
            raise ValueError("DECISION_AS_OF_TIME_REQUIRED")
        if options.stock_limit is not None and not 1 <= options.stock_limit <= int(self.config["screening"]["input_top_n"]):
            raise ValueError("STOCK_LIMIT_OUT_OF_RANGE")
        if options.enable_real_search and (options.mock_search or options.no_network):
            raise ValueError("SEARCH_MODE_CONFLICT")
        decision_as_of = options.decision_as_of_time.astimezone(SHANGHAI)
        now = datetime.now(SHANGHAI)
        if decision_as_of > now + timedelta(minutes=5):
            raise ValueError("DECISION_AS_OF_TIME_IN_FUTURE")
        if options.trade_date > decision_as_of.date():
            raise ValueError("BASE_TRADE_DATE_AFTER_DECISION_TIME")
        if options.enable_real_search and decision_as_of.date() != now.date():
            raise ValueError("HISTORICAL_REPLAY_FORBIDS_DIRECT_SEARCH")
        if options.enable_real_search:
            effective_limit = self._effective_stock_limit(options)
            is_canary = 1 <= effective_limit <= REAL_SEARCH_CANARY_MAX
            is_confirmed_full = (
                effective_limit == FULL_SEARCH_SIZE and options.confirm_full_search
            )
            if not (is_canary or is_confirmed_full):
                raise ValueError(
                    "REAL_SEARCH_REQUIRES_CANARY_OR_CONFIRMED_FULL_TOP100"
                )
        if options.enable_real_search and not options.skip_pro:
            raise ValueError("REAL_CANARY_PRO_MUST_BE_DISABLED")

    def _effective_stock_limit(self, options: RunOptions) -> int:
        return int(options.stock_limit or self.config["screening"]["input_top_n"])

    def _validate_preopen_contract(
        self,
        options: RunOptions,
        source: dict[str, Any],
    ) -> None:
        if not options.enable_real_search:
            return
        raw_target = str(source.get("monday_trade_date") or "").strip()
        if not raw_target:
            raise ValueError("TARGET_TRADE_DATE_REQUIRED_FOR_PREOPEN_SEARCH")
        target_trade_date = date.fromisoformat(raw_target)
        if options.trade_date >= target_trade_date:
            raise ValueError("TARGET_TRADE_DATE_MUST_FOLLOW_BASE_TRADE_DATE")
        decision_as_of = options.decision_as_of_time.astimezone(SHANGHAI)
        cutoff = datetime.combine(target_trade_date, PREOPEN_CUTOFF, tzinfo=SHANGHAI)
        if decision_as_of >= cutoff:
            raise ValueError("PREOPEN_DECISION_CUTOFF_EXCEEDED")
        if datetime.now(SHANGHAI) >= cutoff:
            raise ValueError("REAL_SEARCH_EXECUTION_AFTER_PREOPEN_CUTOFF")
        if (target_trade_date - options.trade_date).days > 4:
            raise ValueError("HISTORICAL_REPLAY_FORBIDS_DIRECT_SEARCH")

    def _assert_successful_canary(
        self,
        options: RunOptions,
        *,
        source: dict[str, Any],
        source_file_hash: str,
        universe_file_hash: str,
        prompt_hash: str,
    ) -> None:
        output_root = (
            self.root / "outputs" / "event_overlay" / options.trade_date.isoformat()
        )
        candidates: list[dict[str, Any]] = []
        for path in sorted(output_root.glob("*/run_manifest.json")):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            source_artifact = manifest.get("source_artifact") or {}
            universe_artifact = manifest.get("universe_source_artifact") or {}
            completed = (
                manifest.get("final_status") == "V3_EVENT_OVERLAY_SHADOW_READY"
                and bool(manifest.get("real_search_enabled"))
                and int(manifest.get("input_count") or 0) == REAL_SEARCH_CANARY_MAX
                and int(manifest.get("search_failure_count") or 0) == 0
                and int(manifest.get("provider_failure_count") or 0) == 0
                and int(manifest.get("successful_evaluation_count") or 0)
                == REAL_SEARCH_CANARY_MAX
                and int(manifest.get("web_search_request_count") or 0) > 0
                and (
                    int(manifest.get("actual_network_calls") or 0)
                    + int(manifest.get("reused_checkpoint_count") or 0)
                ) >= REAL_SEARCH_CANARY_MAX
                and str(manifest.get("source_run_id") or "")
                == str(source.get("run_id") or "")
                and str(source_artifact.get("sha256") or "") == source_file_hash
                and str(universe_artifact.get("sha256") or "")
                == universe_file_hash
                and str(manifest.get("prompt_hash") or "") == prompt_hash
                and str(manifest.get("search_contract_version") or "")
                == SEARCH_CONTRACT_VERSION
                and str(manifest.get("checkpoint_contract_version") or "")
                == CHECKPOINT_CONTRACT_VERSION
            )
            if completed:
                candidates.append(manifest)
        if not candidates:
            raise ValueError("SUCCESSFUL_REAL_SEARCH_CANARY_REQUIRED_FOR_FULL_TOP100")

    def _provider(self, options: RunOptions) -> EventSearchProvider:
        if options.mock_search:
            return MockEventSearchProvider()
        if options.enable_real_search:
            web = self.config.get("web_search") or {}
            return DeepSeekFlashDirectSearchProvider(
                self.prompt,
                endpoint=str(web.get("endpoint") or ""),
                model=str(web.get("model") or "deepseek-v4-flash"),
                max_uses=int(web.get("max_uses") or 3),
                max_tokens=int(web.get("max_tokens") or 6000),
                max_continuations=int(web.get("max_continuations") or 2),
            )
        return NoNetworkEventSearchProvider(
            historical=options.trade_date != datetime.now(SHANGHAI).date()
        )

    def _checkpoint_context(
        self,
        stock: dict[str, Any],
        *,
        options: RunOptions,
        source: dict[str, Any],
        prompt_hash: str,
    ) -> tuple[str, str, str, EventOverlayCheckpointContract]:
        code = str(stock["stock_code"]).zfill(6)
        stock["stock_code"] = code
        query = self._query(stock, options.decision_as_of_time)
        stock_input_hash = canonical_hash({
            "stock": stock,
            "query": query,
            "decision_as_of_time": options.decision_as_of_time,
            "screening_version": SCREENING_VERSION,
        })
        contract = EventOverlayCheckpointContract(
            trade_date=options.trade_date.isoformat(),
            stock_code=code,
            factor_version=str(source["factor_version"]),
            input_hash=stock_input_hash,
            prompt_version=str(self.prompt["prompt_version"]),
            prompt_hash=prompt_hash,
            schema_version=EVENT_REVIEW_VERSION,
            contract_version=SEARCH_CONTRACT_VERSION,
            data_manifest_hash=canonical_hash(source.get("data_quality") or {}),
            membership_manifest_hash=canonical_hash({
                "industry": source.get("industry_members") or {},
                "concept": source.get("concept_members") or {},
            }),
            fundamental_snapshot_hash=canonical_hash(stock.get("fundamental") or {}),
            news_snapshot_hash=canonical_hash({"query": query}),
            overseas_snapshot_hash=canonical_hash({"status": "DISABLED"}),
            market_regime_hash=canonical_hash(self._market_regime(options.trade_date)),
            risk_version=RISK_VERSION,
            decision_as_of_time=options.decision_as_of_time,
            search_mode=(
                "MOCK_SEARCH" if options.mock_search
                else "REAL_DIRECT_SEARCH" if options.enable_real_search
                else "NO_NETWORK"
            ),
            search_query_hash=canonical_hash(query),
            screening_config_hash=canonical_hash({
                "screening": self.config["screening"],
                "scoring": self.config["scoring"],
                "freshness": self.config["freshness"],
                "web_search": self.config.get("web_search") or {},
            }),
            source_tier_policy_hash=canonical_hash(self.config["source_tiers"]),
        )
        return code, query, stock_input_hash, contract

    def _load_source(self, trade_date: date, run_id: str | None) -> tuple[Path, dict[str, Any]]:
        path = self.root / "outputs" / "quant_v2_validation" / trade_date.isoformat() / "quant_v2_validation.json"
        if not path.is_file():
            raise FileNotFoundError(f"V2_QUANT_SOURCE_NOT_FOUND:{path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if run_id and payload.get("run_id") != run_id:
            raise ValueError("BASE_QUANT_RUN_ID_MISMATCH")
        stage = ((payload.get("v2_run") or {}).get("stages") or {}).get("TUSHARE_QUANT_V2_CORRECTED_SHADOW")
        if not stage or len(stage.get("top100") or []) != 100:
            raise ValueError("BASE_V2_TOP100_NOT_FROZEN_OR_INCOMPLETE")
        return path, payload

    def _load_eligible_universe(
        self,
        source_path: Path,
        source: dict[str, Any],
        trade_date: date,
    ) -> tuple[Path, list[dict[str, Any]]]:
        """Load the ranked, hard-gate-free V2 universe used as V3 input.

        The frozen stage Top100 can contain stocks that later failed the V2 hard
        gate.  V3 must therefore refill from the immutable full-universe export,
        in Quant rank order, instead of consuming that stage list directly.
        """
        expected_count = int(self.config["screening"]["input_top_n"])
        expected_date = trade_date.isoformat()
        source_date = _normalize_trade_date(source.get("trade_date"))
        if source_date != expected_date or source_path.parent.name != expected_date:
            raise ValueError("BASE_V2_FULL_UNIVERSE_TRADE_DATE_MISMATCH")

        expected_factor_version = str(source.get("factor_version") or "").strip()
        if not expected_factor_version:
            raise ValueError("BASE_V2_FACTOR_VERSION_REQUIRED")

        path = source_path.parent / "v2_full_universe.csv"
        if not path.is_file():
            raise FileNotFoundError(f"V2_FULL_UNIVERSE_NOT_FOUND:{path}")

        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            required = {
                "rank",
                "stock_code",
                "total_score",
                "hard_gate",
                "hard_gate_reasons",
                "factor_version",
            }
            missing = sorted(required - fields)
            if missing:
                raise ValueError(
                    "BASE_V2_FULL_UNIVERSE_COLUMNS_MISSING:" + ",".join(missing)
                )
            raw_rows = [dict(row) for row in reader]

        if not raw_rows:
            raise ValueError("BASE_V2_FULL_UNIVERSE_EMPTY")

        if "trade_date" in fields:
            row_dates = [_normalize_trade_date(row.get("trade_date")) for row in raw_rows]
            if any(value != expected_date for value in row_dates):
                raise ValueError("BASE_V2_FULL_UNIVERSE_TRADE_DATE_MISMATCH")

        normalized: list[dict[str, Any]] = []
        seen_ranks: set[int] = set()
        seen_codes: set[str] = set()
        for row in raw_rows:
            factor_version = str(row.get("factor_version") or "").strip()
            if factor_version != expected_factor_version:
                raise ValueError("BASE_V2_FULL_UNIVERSE_FACTOR_VERSION_MISMATCH")
            try:
                rank_value = float(str(row.get("rank") or "").strip())
                rank = int(rank_value)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("BASE_V2_FULL_UNIVERSE_RANK_INVALID") from None
            if rank < 1 or rank_value != rank or rank in seen_ranks:
                raise ValueError("BASE_V2_FULL_UNIVERSE_RANK_INVALID")
            code = str(row.get("stock_code") or "").split(".")[0].strip().zfill(6)
            if not code.isdigit() or len(code) != 6 or code in seen_codes:
                raise ValueError("BASE_V2_FULL_UNIVERSE_STOCK_CODE_INVALID")
            seen_ranks.add(rank)
            seen_codes.add(code)
            row["rank"] = rank
            row["stock_code"] = code
            try:
                total_score = float(str(row.get("total_score") or "").strip())
            except (TypeError, ValueError):
                raise ValueError(
                    "BASE_V2_FULL_UNIVERSE_TOTAL_SCORE_INVALID"
                ) from None
            if not math.isfinite(total_score):
                raise ValueError("BASE_V2_FULL_UNIVERSE_TOTAL_SCORE_INVALID")
            row["total_score"] = total_score
            row["hard_gate"] = _parse_hard_gate_flag(row.get("hard_gate"))
            row["hard_gate_reasons"] = _parse_hard_gate_reasons(
                row.get("hard_gate_reasons")
            )
            normalized.append(row)

        if sorted(seen_ranks) != list(range(1, len(normalized) + 1)):
            raise ValueError("BASE_V2_FULL_UNIVERSE_RANK_SEQUENCE_INVALID")

        eligible = sorted(
            (
                row
                for row in normalized
                if not row["hard_gate"] and not row["hard_gate_reasons"]
            ),
            key=lambda row: (row["rank"], row["stock_code"]),
        )
        if len(eligible) < expected_count:
            raise ValueError(
                "BASE_V2_HARD_GATE_FREE_UNIVERSE_INSUFFICIENT:"
                f"{len(eligible)}/{expected_count}"
            )
        return path, eligible[:expected_count]

    @staticmethod
    def _assert_ranked_hard_gate_integrity(items: list[Any]) -> None:
        violations = []
        for item in items:
            if not item.selected_top20:
                continue
            reasons = _parse_hard_gate_reasons(item.hard_gate_reasons)
            raw_quant = item.raw_quant or {}
            if reasons or _parse_hard_gate_flag(raw_quant.get("hard_gate", False)):
                violations.append(str(item.stock_code))
        if violations:
            raise RuntimeError(
                "V3_SELECTED_TOP20_HARD_GATE_VIOLATION:" + ",".join(violations)
            )

    def _query(self, stock: dict[str, Any], decision_as_of_time: datetime) -> str:
        raw_name = str(stock.get("stock_name") or "").strip()
        stock_name = raw_name if not _looks_mojibake(raw_name) else "名称待源数据修复"
        return (
            f"股票代码 {stock['stock_code']}，股票名称 {stock_name}。"
            f"检索截至 {decision_as_of_time.isoformat()} 已公开的最新重大事件。"
            "市场新闻仅限36小时，普通公司事件仅限72小时，交易所、公司或政府正式公告仅限168小时；"
            "无合格新消息时返回空events，不得用旧新闻或重复转载凑数。"
            "一次搜索覆盖业绩、订单、并购、监管处罚、事故、产品价格、解禁、减持和质押；"
            "优先官方来源，返回原始URL和带时区发布时间，不得使用决策时点之后的信息。"
        )

    def _v2_top20_codes(self, trade_date: date, top100: list[dict[str, Any]]) -> list[str]:
        audit_path = self.root / "outputs" / "quant_v2_validation" / trade_date.isoformat() / "monday_v2_candidate_audit.json"
        if audit_path.is_file():
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            codes = list((audit.get("flash") or {}).get("top20_codes") or [])
            if codes:
                return [str(code).split(".")[0].zfill(6) for code in codes[:20]]
        return [str(row["stock_code"]).zfill(6) for row in top100[:20]]

    def _market_regime(self, trade_date: date) -> dict[str, Any]:
        path = self.root / "outputs" / "quant_v2_validation" / trade_date.isoformat() / "monday_v2_candidate_audit.json"
        if not path.is_file():
            return {"status": "NOT_AVAILABLE", "deployment_policy": "PRESERVE_V2"}
        payload = json.loads(path.read_text(encoding="utf-8"))
        regime = payload.get("market_regime") or {}
        return {
            "status": regime.get("status") or regime.get("regime") or "NOT_AVAILABLE",
            "source": "EXISTING_V2_AUDIT",
            "deployment_policy": "PRESERVE_V2",
            "raw": regime,
        }

    def _frozen_decision_hashes(self) -> dict[str, str]:
        root = (
            self.root
            / "outputs"
            / "quant_v2_validation"
            / "2026-07-24"
            / "frozen_decisions"
        )
        return {
            str(path.relative_to(self.root)): file_hash(path)
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def _comparison(
        self,
        items: list[dict[str, Any]],
        v2_codes: list[str],
        snapshots: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        v2_rank = {code: index for index, code in enumerate(v2_codes, start=1)}
        snapshots_by_id = {row["snapshot_id"]: row for row in snapshots}
        rows = []
        for item in items:
            snapshot = snapshots_by_id[item["event_snapshot_id"]]
            events = snapshot.get("items") or []
            old_rank = v2_rank.get(item["stock_code"])
            delta = (old_rank - item["v3_rank"]) if old_rank else None
            if old_rank is None and item["selected_top20"]:
                event_action = "PROMOTE"
            elif old_rank is not None and not item["selected_top20"]:
                event_action = "DEMOTE"
            elif item["risk_action"] in {"WATCH_ONLY", "BLOCK"}:
                event_action = item["risk_action"]
            else:
                event_action = "KEEP"
            rows.append({
                "股票代码": item["stock_code"],
                "股票名称": item["stock_name"],
                "Quant排名": item["quant_rank"],
                "Quant分": item["quant_score"],
                "V2初筛排名": old_rank,
                "V3初筛排名": item["v3_rank"],
                "排名变化": delta,
                "Event Opportunity Score": item["event_opportunity_score"],
                "Evidence Confidence": item["evidence_confidence"],
                "Event Action": event_action,
                "Risk Action": item["risk_action"],
                "事件摘要": "；".join(event["summary"] for event in events[:2]),
                "主要来源": "；".join((event.get("domain") or event["provider"]) for event in events[:2]),
                "来源发布时间": "；".join(str(event.get("published_at") or "") for event in events[:2]),
                "是否联网搜索降级": snapshot["direct_search_used"],
                "最终是否进入V3 Top20": item["selected_top20"],
            })
        return rows

    def _persist(
        self,
        manifest: dict[str, Any],
        items: list[dict[str, Any]],
        snapshots: list[dict[str, Any]],
        issues: list[dict[str, Any]],
    ) -> None:
        run = EventScreeningRunRecord(
            **{
                key: manifest[key]
                for key in (
                    "run_id", "source_run_id",
                    "source_input_hash", "universe_snapshot_id", "factor_version",
                    "screening_version", "decision_version", "production_or_shadow",
                    "execution_mode", "real_search_enabled", "historical_replay",
                    "input_count", "output_count", "actual_network_calls",
                    "logical_evaluations", "reused_checkpoint_count",
                    "stale_checkpoint_count", "content_hash",
                )
            },
            trade_date=date.fromisoformat(str(manifest["trade_date"])),
            decision_as_of_time=_parse_datetime(manifest["decision_as_of_time"]),
            manifest_json=manifest,
        )
        self.session.add(run)
        self.session.flush()
        snapshot_pk: dict[str, int] = {}
        for snapshot in snapshots:
            row = EventEvidenceSnapshotRecord(
                **{key: snapshot[key] for key in (
                    "snapshot_id", "run_id",
                    "stock_code", "query", "search_status", "provider",
                    "provider_verified", "direct_search_used",
                    "confidence_discount_applied", "production_eligible",
                    "shadow_eligible", "input_hash", "content_hash", "contract_version",
                )},
                trade_date=date.fromisoformat(str(snapshot["trade_date"])),
                decision_as_of_time=_parse_datetime(snapshot["decision_as_of_time"]),
            )
            self.session.add(row)
            self.session.flush()
            snapshot_pk[snapshot["snapshot_id"]] = row.id
            for event in snapshot.get("items") or []:
                self.session.add(EventEvidenceItemRecord(
                    snapshot_id=row.id,
                    **{key: event[key] for key in (
                        "event_id", "event_cluster_id", "event_type", "title", "summary",
                        "url", "canonical_url", "domain",
                        "source_tier", "source_type", "provider", "provider_verified",
                        "event_direction", "materiality", "relevance", "confidence",
                        "temporal_status", "content_hash", "revision",
                    )},
                    published_at=(
                        _parse_datetime(event["published_at"])
                        if event.get("published_at") else None
                    ),
                    retrieved_at=_parse_datetime(event["retrieved_at"]),
                    raw_metadata_json=event,
                ))
        for item in items:
            self.session.add(EventScreeningItemRecord(
                screening_run_id=run.id,
                **{key: item[key] for key in (
                    "stock_code", "stock_name", "quant_rank", "quant_score",
                    "event_opportunity_score", "evidence_confidence", "evidence_breadth",
                    "risk_action", "v3_screening_score", "v3_rank", "selected_top20",
                    "search_status", "event_snapshot_id", "checkpoint_status",
                )},
                hard_gate_reasons_json=item.get("hard_gate_reasons") or [],
                raw_quant_json=item.get("raw_quant") or {},
            ))
            snapshot = next(row for row in snapshots if row["snapshot_id"] == item["event_snapshot_id"])
            self.session.add(EventReviewResultRecord(
                run_id=manifest["run_id"],
                snapshot_id=snapshot_pk[item["event_snapshot_id"]],
                stock_code=item["stock_code"],
                search_status=item["search_status"],
                event_opportunity_score=item["event_opportunity_score"],
                evidence_confidence=item["evidence_confidence"],
                breadth_score=item["evidence_breadth"],
                risk_action=item["risk_action"],
                conflicts_json=snapshot.get("review_conflicts") or [],
                warnings_json=snapshot.get("review_warnings") or [],
                review_version=EVENT_REVIEW_VERSION,
            ))
        for issue in issues:
            issue_hash = canonical_hash({"run_id": manifest["run_id"], **issue})
            self.session.add(EventOverlayDataIssueRecord(
                issue_id=f"issue-{issue_hash[:24]}",
                issue_hash=issue_hash,
                run_id=manifest["run_id"],
                stock_code=issue.get("stock_code"),
                issue_code=issue["issue_code"],
                issue_level=issue["issue_level"],
                detail=issue["detail"],
                detected_at=datetime.now(SHANGHAI),
            ))
        self.session.commit()


def default_decision_as_of(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time(15, 30), tzinfo=SHANGHAI)


def _issue(stock_code: str | None, code: str, level: str, detail: str) -> dict[str, Any]:
    return {
        "stock_code": stock_code,
        "issue_code": code,
        "issue_level": level,
        "detail": detail,
    }


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _normalize_trade_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return ""


def _parse_hard_gate_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raw = str(value or "").strip().lower()
    if raw in {"false", "0"}:
        return False
    if raw in {"true", "1"}:
        return True
    raise ValueError("BASE_V2_FULL_UNIVERSE_HARD_GATE_INVALID")


def _parse_hard_gate_reasons(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(reason).strip() for reason in value if str(reason).strip()]
    raw = str(value).strip()
    if not raw or raw.lower() in {"none", "null"}:
        return []
    if raw.startswith("[") or raw.startswith("("):
        try:
            parsed = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            raise ValueError(
                "BASE_V2_FULL_UNIVERSE_HARD_GATE_REASONS_INVALID"
            ) from None
        if not isinstance(parsed, (list, tuple, set)):
            raise ValueError("BASE_V2_FULL_UNIVERSE_HARD_GATE_REASONS_INVALID")
        return [str(reason).strip() for reason in parsed if str(reason).strip()]
    return [reason.strip() for reason in raw.split(";") if reason.strip()]


def _looks_mojibake(value: str) -> bool:
    if not value:
        return False
    markers = ("�", "鍗", "鑲", "杞", "浠", "鈥", "銆", "绔", "娴")
    return any(marker in value for marker in markers)
