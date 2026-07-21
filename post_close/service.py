from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import Counter
from datetime import date, datetime, time as clock_time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend.core.config import AppConfig, get_app_config
from database.models import (
    DecisionSnapshot,
    IFindEnhancementRun,
    IFindShadowAcceptanceRun,
    IFindStockEnhancementScore,
    MarketMinuteBarShadow,
    MarketSnapshotShadow,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
    PostCloseActionResult,
    PostCloseActionRun,
    ProCandidateReview,
    ProResumeRun,
    QuantRankResult,
    QuantRun,
    StockMaster,
    TraderPositionSnapshot,
    PositionTruthConfirmation,
)
from database.models.workbench import ManualSelectionRecord
from post_close.actions import (
    ActionHealthInput,
    HardGateInput,
    PositionFacts,
    PostCloseActionHealthEngine,
    enforce_conservative_pro_review,
    target_day_sellable,
)
from post_close.positions import PositionImportService
from post_close.gates import PositionTruthGate, PostCloseActionPoolResolver
from post_close.coverage import IFindTieredCoverageService
from post_close.scoring import (
    CrossProviderFeaturePolicy,
    IFindDataQuality,
    IFindEodEnhancementEngine,
    MinutePoint,
    ScoringProfile,
)
from services.ifind_shadow_service import RealtimeMonitorPoolResolver
from services.ifind_shadow_acceptance_service import IFindShadowAcceptanceService
from stock_codes import normalize_ts_code
from temporal.calendar import TradeCalendarService
from temporal.watermarks import DatasetWatermarkService


SHANGHAI = ZoneInfo("Asia/Shanghai")


class IFindEnhancementService:
    def __init__(self, session, app_config: AppConfig | None = None) -> None:
        self.session = session
        self.app_config = app_config or get_app_config()
        self.config = self.app_config.config_files.get("post_close_action", {}).get("ifind_eod_enhancement", {})
        self.engine = IFindEodEnhancementEngine(self.config)
        self.feature_policy = CrossProviderFeaturePolicy(self.config.get("cross_provider_feature_policy", {}))

    def run_shadow(self, trade_date: date, *, quant_run_id: str | None = None, provider_run_id: str | None = None) -> dict[str, Any]:
        quant = self._quant_run(trade_date, quant_run_id)
        top_n = max(1, min(int(self.config.get("top_n", 100)), 100))
        base_rows = list(self.session.scalars(select(QuantRankResult).where(
            QuantRankResult.quant_run_id == quant.run_id,
        ).order_by(QuantRankResult.rank).limit(top_n)))
        if not base_rows:
            raise ValueError("QUANT_BASELINE_NOT_FOUND")
        provider_id = provider_run_id or self._provider_run_id(trade_date)
        input_hash = self._input_hash(trade_date, quant.run_id, provider_id, base_rows)
        existing = self.session.scalar(select(IFindEnhancementRun).where(IFindEnhancementRun.input_hash == input_hash))
        if existing and existing.status == "SUCCESS":
            return self.summary(existing.run_id, cache_status="HIT")

        run_id = f"ifind-enh-{uuid.uuid4().hex[:20]}"
        run = IFindEnhancementRun(
            run_id=run_id, trade_date=trade_date, quant_run_id=quant.run_id,
            provider_run_id=provider_id, mode="SHADOW", status="RUNNING", input_hash=input_hash,
            stock_count=len(base_rows), coverage_ratio=0, material_conflict_count=0,
            feature_version=str(self.config.get("feature_version", "ifind_eod_shadow_v1")),
            scoring_profile=ScoringProfile.IFIND_SHADOW_V1,
            cohort_snapshot_json={},
        )
        self.session.add(run)
        self.session.flush()

        daily = self._tushare_daily(trade_date)
        minute_by_code = self._minute_rows(trade_date, [row.stock_code for row in base_rows])
        snapshot_by_code = self._latest_snapshots(trade_date, [row.stock_code for row in base_rows])
        provisional: list[dict[str, Any]] = []
        covered = 0
        conflicts = 0
        for base in base_rows:
            code = normalize_ts_code(base.stock_code)
            official = daily.get(code)
            snapshot = snapshot_by_code.get(code)
            minute_rows = minute_by_code.get(code, [])
            dual_status = _dual_status(snapshot, official)
            conflicts += dual_status == "MATERIAL_CONFLICT"
            features = None
            quality = None
            if snapshot is not None and official is not None:
                stock_return = _return(float(official.get("close") or 0), float(official.get("pre_close") or 0))
                features = self.engine.features_from_snapshot(
                    stock_return=stock_return, index_return=0.0,
                    close=float(snapshot.latest or 0), high=float(snapshot.high or 0), low=float(snapshot.low or 0),
                )
                if features is not None:
                    covered += 1
                    quality = IFindDataQuality(
                        freshness_score=1.0 if snapshot.data_status in {"CLOSED_SESSION_FINAL", "PASS", "REALTIME"} else 0.7,
                        coverage_score=1.0,
                        minute_completeness_score=1.0,
                        consistency_score=0.0 if dual_status == "MATERIAL_CONFLICT" else 1.0,
                        timestamp_quality_score=1.0 if snapshot.provider_time else 0.0,
                    )
            score = self.engine.score(float(base.total_score), features, quality, dual_source_status=dual_status, minute_available=False)
            provisional.append({"base": base, "score": score, "dual_status": dual_status, "minute_completeness": None})

        ranked = sorted(provisional, key=lambda item: (-item["score"].enhanced_score, item["base"].rank, normalize_ts_code(item["base"].stock_code)))
        enhanced_rank = {normalize_ts_code(item["base"].stock_code): index for index, item in enumerate(ranked, start=1)}
        for item in provisional:
            base, score = item["base"], item["score"]
            components = score.component_scores
            self.session.add(IFindStockEnhancementScore(
                run_id=run_id, stock_code=normalize_ts_code(base.stock_code), base_score=base.total_score, base_rank=base.rank,
                ifind_eod_score=score.ifind_eod_score, overlay_delta=score.overlay_delta, enhanced_score=score.enhanced_score,
                enhanced_rank=enhanced_rank[normalize_ts_code(base.stock_code)],
                relative_strength_score=components.get("relative_strength"), close_quality_score=components.get("close_quality"),
                tail_strength_score=components.get("tail_strength"), intraday_stability_score=components.get("intraday_stability"),
                liquidity_confirmation_score=components.get("liquidity_confirmation"), regime_fit_score=components.get("market_regime_fit"),
                component_scores_json={**components, "feature_scope": "PARTIAL_SNAPSHOT_ONLY"}, data_quality_coefficient=score.data_quality_coefficient,
                freshness_status="PASS" if score.fallback_reason is None else "NOT_AVAILABLE",
                minute_completeness=item["minute_completeness"], dual_source_status=item["dual_status"],
                scoring_profile=score.scoring_profile, feature_version=run.feature_version, fallback_reason=score.fallback_reason,
            ))
        coverage = covered / len(base_rows)
        comparable = coverage >= float(self.config.get("minimum_coverage_ratio", .95))
        base_top20 = [normalize_ts_code(row.stock_code) for row in base_rows[:20]]
        enhanced_top20 = [normalize_ts_code(item["base"].stock_code) for item in ranked[:20]]
        run.coverage_ratio = coverage
        run.material_conflict_count = conflicts
        run.status = "SUCCESS"
        run.completed_at = datetime.now(timezone.utc)
        run.cohort_snapshot_json = {
            "status": "IMMUTABLE" if comparable else "NOT_COMPARABLE_COVERAGE_MISMATCH",
            "baseline_cohort": "BASELINE_TOP20", "shadow_cohort": "IFIND_PARTIAL_TOP20",
            "baseline_top20": base_top20, "enhanced_top20": enhanced_top20 if comparable else [],
            "overlap": sorted(set(base_top20) & set(enhanced_top20)) if comparable else [],
            "coverage_ratio": coverage, "minimum_coverage_ratio": float(self.config.get("minimum_coverage_ratio", .95)),
        }
        self.session.commit()
        return self.summary(run_id, cache_status="MISS")

    def summary(self, run_id: str, *, cache_status: str = "UNKNOWN") -> dict[str, Any]:
        run = self.session.scalar(select(IFindEnhancementRun).where(IFindEnhancementRun.run_id == run_id))
        if run is None:
            raise ValueError("IFIND_ENHANCEMENT_RUN_NOT_FOUND")
        rows = list(self.session.scalars(select(IFindStockEnhancementScore).where(
            IFindStockEnhancementScore.run_id == run_id,
        ).order_by(IFindStockEnhancementScore.base_rank)))
        rank_changes = [row.base_rank - row.enhanced_rank for row in rows]
        comparable = float(run.coverage_ratio) >= float(self.config.get("minimum_coverage_ratio", .95))
        base_top20 = {row.stock_code for row in rows if row.base_rank <= 20}
        enhanced_top20 = {row.stock_code for row in rows if row.enhanced_rank <= 20} if comparable else set()
        return {
            "run_id": run.run_id, "trade_date": run.trade_date, "quant_run_id": run.quant_run_id,
            "status": run.status, "mode": run.mode, "scoring_profile": run.scoring_profile,
            "stock_count": len(rows), "coverage_ratio": float(run.coverage_ratio),
            "material_conflict_count": run.material_conflict_count,
            "base_top20": sorted(base_top20), "enhanced_top20": sorted(enhanced_top20),
            "top20_overlap": len(base_top20 & enhanced_top20) if comparable else None, "top100_overlap": len(rows) if comparable else None,
            "rank_correlation": _spearman(rows) if comparable else None, "median_rank_change": median(rank_changes) if comparable and rank_changes else None,
            "maximum_rank_change": max((abs(value) for value in rank_changes), default=0) if comparable else None,
            "overlay_distribution": _distribution([float(row.overlay_delta) for row in rows]),
            "ifind_score_distribution": _distribution([float(row.ifind_eod_score) for row in rows if row.ifind_eod_score is not None]),
            "fallback_count": sum(row.fallback_reason is not None for row in rows), "cache_status": cache_status,
            "official_ranking_changed": False, "cohort_snapshot": run.cohort_snapshot_json,
            "feature_scope": "PARTIAL_SNAPSHOT_ONLY",
            "ab_comparability": "COMPARABLE" if comparable else "NOT_COMPARABLE_COVERAGE_MISMATCH",
            "provisional_ranking_saved_for_audit": True,
        }

    def rows(self, run_id: str) -> list[IFindStockEnhancementScore]:
        return list(self.session.scalars(select(IFindStockEnhancementScore).where(IFindStockEnhancementScore.run_id == run_id).order_by(IFindStockEnhancementScore.enhanced_rank)))

    def _quant_run(self, trade_date: date, run_id: str | None) -> QuantRun:
        query = select(QuantRun).where(QuantRun.base_market_trade_date == trade_date, QuantRun.status.in_(["SUCCESS", "COMPLETED"]))
        if run_id:
            query = query.where(QuantRun.run_id == run_id)
        row = self.session.scalar(query.order_by(QuantRun.created_at.desc()))
        if row is None:
            raise ValueError("QUANT_RUN_NOT_FOUND")
        return row

    def _provider_run_id(self, trade_date: date) -> str | None:
        row = self.session.scalar(select(IFindShadowAcceptanceRun).where(
            IFindShadowAcceptanceRun.trade_date == trade_date,
            IFindShadowAcceptanceRun.status.in_(["CLOSED_SESSION_ACCEPTED", "OPEN_SESSION_ACCEPTED"]),
        ).order_by(IFindShadowAcceptanceRun.started_at.desc()))
        return row.acceptance_run_id if row else None

    def _input_hash(self, trade_date: date, quant_run_id: str, provider_run_id: str | None, rows: Iterable[QuantRankResult]) -> str:
        payload = {
            "trade_date": trade_date.isoformat(), "quant_run_id": quant_run_id, "provider_run_id": provider_run_id,
            "scores": [(normalize_ts_code(row.stock_code), row.rank, str(row.total_score)) for row in rows],
            "config": self.config, "policy": self.feature_policy.as_dict(),
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _minute_rows(self, trade_date: date, codes: list[str]) -> dict[str, list[MarketMinuteBarShadow]]:
        start = datetime.combine(trade_date, clock_time.min, tzinfo=SHANGHAI)
        end = start + timedelta(days=1)
        values = self.session.scalars(select(MarketMinuteBarShadow).where(
            MarketMinuteBarShadow.stock_code.in_([normalize_ts_code(code) for code in codes]),
            MarketMinuteBarShadow.bar_time >= start, MarketMinuteBarShadow.bar_time < end,
            MarketMinuteBarShadow.provider == "IFIND_HTTP",
        ).order_by(MarketMinuteBarShadow.stock_code, MarketMinuteBarShadow.bar_time)).all()
        result: dict[str, list[MarketMinuteBarShadow]] = {}
        for row in values:
            result.setdefault(normalize_ts_code(row.stock_code), []).append(row)
        return result

    def _latest_snapshots(self, trade_date: date, codes: list[str]) -> dict[str, MarketSnapshotShadow]:
        values = self.session.scalars(select(MarketSnapshotShadow).where(
            MarketSnapshotShadow.stock_code.in_([normalize_ts_code(code) for code in codes]),
            MarketSnapshotShadow.provider == "IFIND_HTTP",
        ).order_by(MarketSnapshotShadow.snapshot_time.desc())).all()
        result: dict[str, MarketSnapshotShadow] = {}
        for row in values:
            stamp = _aware(row.snapshot_time)
            code = normalize_ts_code(row.stock_code)
            if stamp.date() == trade_date and code not in result:
                result[code] = row
        return result

    def _tushare_daily(self, trade_date: date) -> dict[str, dict[str, Any]]:
        path = Path(self.app_config.root_dir) / "data" / "cache" / "tushare" / "trade_date" / "daily" / f"{trade_date:%Y%m%d}.json"
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {normalize_ts_code(row["ts_code"]): row for row in values if isinstance(row, dict) and row.get("ts_code")}


class PostCloseActionService:
    def __init__(self, session, app_config: AppConfig | None = None) -> None:
        self.session = session
        self.app_config = app_config or get_app_config()
        self.config = self.app_config.config_files.get("post_close_action", {}).get("post_close_action", {})
        self.engine = PostCloseActionHealthEngine(self.config)
        self.positions = PositionImportService(session)

    def position_truth(self, trade_date: date, *, include_ai_simulation: bool = False, now: datetime | None = None) -> dict[str, Any]:
        root = self.app_config.config_files.get("post_close_action", {})
        truth_config = root.get("position_truth", {})
        virtual_config = self.app_config.config_files.get("virtual_trading", {}).get("virtual_trading", {})
        requested = {"AI_SIMULATION"} if include_ai_simulation else set()
        return PositionTruthGate(
            self.session,
            config=truth_config,
            ai_simulation_enabled=bool(virtual_config.get("enabled", False)),
            requested_scopes=requested,
            now=now,
        ).evaluate(trade_date)

    def gate(
        self,
        trade_date: date,
        run_mode: str,
        *,
        allow_historical: bool = False,
        include_ai_simulation: bool = False,
        require_ifind_ready: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        local = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        market_session = _market_session(local, trade_date)
        missing: list[str] = []
        position_truth = self.position_truth(trade_date, include_ai_simulation=include_ai_simulation, now=local)
        pool = PostCloseActionPoolResolver(self.session).resolve(trade_date, mode=run_mode)
        if not allow_historical and market_session not in {"POST_MARKET", "CLOSED"}:
            missing.append("POST_CLOSE_SESSION_REQUIRED")
        if not position_truth["action_run_allowed"]:
            missing.append("POSITION_SNAPSHOT_REQUIRED")
        if pool["invariant_status"] != "PASS":
            missing.append("ACTION_POOL_COUNT_INVARIANT_VIOLATION")
        if not pool["items"]:
            missing.append("ACTION_POOL_EMPTY")
        tushare_readiness = self._tushare_final_readiness(trade_date)
        if run_mode == "POST_CLOSE_FINAL" and not tushare_readiness["ready"]:
            missing.append("TUSHARE_FINAL_DATA_NOT_READY")
        if run_mode == "POST_CLOSE_FINAL" and (pool.get("source_trade_date") != trade_date or not pool.get("pipeline_run_id")):
            missing.append("CURRENT_PIPELINE_NOT_COMPLETED")
        pool_codes = [item["stock_code"] for item in pool.get("items", [])]
        if require_ifind_ready and not allow_historical and market_session in {"POST_MARKET", "CLOSED"} and not self._ifind_ready(trade_date, pool_codes):
            missing.append("IFIND_CLOSE_SNAPSHOT_NOT_READY")
        if str(self.app_config.env.get("ENABLE_REAL_TRADING", "false")).lower() not in {"false", "0", "off", "no", ""}:
            missing.append("ENABLE_REAL_TRADING=false")
        status = "READY"
        if "POST_CLOSE_SESSION_REQUIRED" in missing: status = "BLOCKED_MARKET_NOT_CLOSED"
        elif "POSITION_SNAPSHOT_REQUIRED" in missing: status = "BLOCKED_POSITION_SNAPSHOT_REQUIRED"
        elif "ACTION_POOL_COUNT_INVARIANT_VIOLATION" in missing: status = "ACTION_POOL_COUNT_INVARIANT_VIOLATION"
        elif "IFIND_CLOSE_SNAPSHOT_NOT_READY" in missing: status = "BLOCKED_IFIND_DATA_UNAVAILABLE"
        elif "TUSHARE_FINAL_DATA_NOT_READY" in missing: status = "BLOCKED_DATA_NOT_READY"
        elif "CURRENT_PIPELINE_NOT_COMPLETED" in missing: status = "BLOCKED_PIPELINE_NOT_COMPLETED"
        elif missing: status = "FAILED"
        return {
            "passed": not missing,
            "missing": missing,
            "status": status,
            "market_session": market_session,
            "current_time": local.isoformat(),
            "position_truth": position_truth,
            "pool": pool,
            "tushare_readiness": tushare_readiness,
        }

    def run_fast(
        self,
        trade_date: date,
        *,
        run_mode: str = "POST_CLOSE_FAST",
        allow_historical: bool = False,
        include_ai_simulation: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        gate = self.gate(
            trade_date,
            run_mode,
            allow_historical=allow_historical,
            include_ai_simulation=include_ai_simulation,
            require_ifind_ready=False,
        )
        if not gate["passed"]:
            status = "WAITING_FOR_POST_CLOSE_RUN" if gate["status"] == "BLOCKED_MARKET_NOT_CLOSED" else gate["status"]
            return {"status": status, "gate": gate, "trade_date": trade_date, "llm_calls": 0, "orders_created": 0}
        fetch_summary = (
            {"status": "HISTORICAL_CACHE_ONLY", "auth_calls": 0, "index_calls": 0, "snapshot_calls": 0, "minute_calls": 0, "retries": 0, "cache_hits": 0, "total": 0, "hard_limit": 0}
            if allow_historical
            else self._ensure_fast_ifind_data(trade_date, gate["pool"])
        )
        gate = self.gate(
            trade_date,
            run_mode,
            allow_historical=allow_historical,
            include_ai_simulation=include_ai_simulation,
            require_ifind_ready=True,
        )
        if not gate["passed"]:
            return {
                "status": gate["status"], "gate": gate, "trade_date": trade_date,
                "ifind_fetch": fetch_summary, "llm_calls": 0, "orders_created": 0,
            }
        started = time.perf_counter()
        pool = self.resolve_pool(trade_date, run_mode=run_mode)
        if not pool["items"]:
            return {"status": "NO_CANDIDATES", "trade_date": trade_date, "llm_calls": 0, "orders_created": 0}
        quant_trade_date = trade_date if run_mode == "POST_CLOSE_FINAL" else pool.get("source_trade_date", trade_date)
        quant_run = self.session.scalar(select(QuantRun).where(QuantRun.base_market_trade_date == quant_trade_date, QuantRun.status.in_(["SUCCESS", "COMPLETED"])).order_by(QuantRun.created_at.desc()))
        enhancement = self.session.scalar(select(IFindEnhancementRun).where(IFindEnhancementRun.trade_date == trade_date, IFindEnhancementRun.status == "SUCCESS").order_by(IFindEnhancementRun.created_at.desc()))
        coverage = IFindTieredCoverageService(self.session, self.app_config).evaluate(trade_date, pool, quant_run)
        input_hash = self._action_input_hash(
            trade_date, run_mode, pool, quant_run, enhancement, coverage, gate["position_truth"]
        )
        existing = self.session.scalar(select(PostCloseActionRun).where(PostCloseActionRun.input_hash == input_hash))
        if existing and existing.status in {"SUCCESS", "FAST_PRELIMINARY_SUCCESS", "FAST_PARTIAL_COVERAGE", "FINAL_MODEL_VALIDATION_SUCCESS", "FINAL_PARTIAL_SUCCESS"} and not force:
            return self.status(existing.run_id, cache_status="HIT")

        run_id = f"post-close-{uuid.uuid4().hex[:20]}"
        target_date = _next_trade_date_from_cache(Path(self.app_config.root_dir), trade_date)
        run = PostCloseActionRun(
            run_id=run_id, trade_date=trade_date, target_trade_date=target_date, run_mode=run_mode,
            status="RUNNING", pipeline_run_id=pool.get("pipeline_run_id"), quant_run_id=quant_run.run_id if quant_run else None,
            enhancement_run_id=enhancement.run_id if enhancement else None, position_snapshot_id=pool.get("position_snapshot_version"),
            market_review_run_id=None, input_hash=input_hash, stock_count=len(pool["items"]), held_count=pool["held_total"],
            non_held_count=pool["non_held_total"], rule_duration_ms=0, pro_review_status="NOT_RUN",
            scoring_profile=ScoringProfile.TUSHARE_BASELINE_V1, config_snapshot_json=_json_safe({**self._safe_config(), "pool": {key: value for key, value in pool.items() if key != "items"}, "tiered_coverage": coverage, "ifind_fetch": fetch_summary, "position_truth": gate["position_truth"]}),
        )
        self.session.add(run)
        self.session.flush()
        data = self._inputs(trade_date, pool, quant_run, enhancement, coverage)
        per_stock_ms = []
        for item in pool["items"]:
            stock_started = time.perf_counter()
            result = self._decide(run_id, trade_date, target_date, item, data)
            self.session.add(result)
            self.session.add(DecisionSnapshot(
                stock_code=result.stock_code, snapshot_time=datetime.now(timezone.utc),
                market_data_json={"trade_date": trade_date.isoformat(), "close_price": _number(result.close_price), "data_quality": result.data_quality_status},
                factor_json={"base_score": _number(result.base_score), "enhanced_shadow_score": _number(result.enhanced_shadow_score), "action_health_score": _number(result.action_health_score)},
                agent_result_json={"baseline_rule_action": result.baseline_rule_action, "ifind_shadow_action": result.ifind_shadow_action, "current_adopted_action": result.current_adopted_action, "advisory_only": True},
                order_price_json={"stop_loss": _number(result.stop_loss_price), "take_profit_1": _number(result.take_profit_1), "take_profit_2": _number(result.take_profit_2), "order_created": False},
                final_score=result.action_health_score, risk_level=result.hard_gate_status, recommendation=result.current_adopted_action,
            ))
            per_stock_ms.append((time.perf_counter() - stock_started) * 1000)
        duration_ms = round((time.perf_counter() - started) * 1000)
        run.rule_duration_ms = duration_ms
        complete = coverage["ab_comparability"] == "COMPARABLE"
        run.status = ("FINAL_MODEL_VALIDATION_SUCCESS" if complete else "FINAL_PARTIAL_SUCCESS") if run_mode == "POST_CLOSE_FINAL" else ("FAST_PRELIMINARY_SUCCESS" if complete else "FAST_PARTIAL_COVERAGE")
        run.completed_at = datetime.now(timezone.utc)
        self.session.commit()
        summary = self.status(run_id, cache_status="MISS")
        summary["performance"] = {
            "stock_count": len(per_stock_ms), "total_duration_ms": duration_ms,
            "p50_per_stock_ms": _percentile(per_stock_ms, 0.50), "p95_per_stock_ms": _percentile(per_stock_ms, 0.95),
            "target_met": duration_ms <= int(self.config.get("fast", {}).get("p95_target_seconds", 30)) * 1000,
        }
        return summary

    def resolve_pool(self, trade_date: date, *, run_mode: str = "POST_CLOSE_FAST") -> dict[str, Any]:
        resolved = PostCloseActionPoolResolver(self.session).resolve(trade_date, mode=run_mode)
        origins: dict[str, set[str]] = {}
        for item in resolved.get("items", []):
            code = normalize_ts_code(item["stock_code"])
            origins.setdefault(code, set()).update(item.get("origins") or [])
        current_positions = list(self.session.scalars(select(TraderPositionSnapshot).where(TraderPositionSnapshot.is_current.is_(True))))
        confirmed_scopes = set(self.session.scalars(select(PositionTruthConfirmation.account_scope).where(
            PositionTruthConfirmation.is_current.is_(True),
            PositionTruthConfirmation.confirmation_status.in_(["CONFIRMED_POSITIONS", "CONFIRMED_EMPTY"]),
        ).distinct()))
        positions: dict[str, list[TraderPositionSnapshot]] = {}
        for row in current_positions:
            code = normalize_ts_code(row.stock_code)
            positions.setdefault(code, []).append(row)
            origins.setdefault(code, set()).add("HUMAN_HELD" if row.account_scope == "HUMAN_REFERENCE" else "AI_HELD")
        items = []
        for code in sorted(origins):
            scopes = {row.account_scope for row in positions.get(code, [])}
            if scopes == {"HUMAN_REFERENCE", "AI_SIMULATION"}: position_status = "BOTH_HELD"
            elif "HUMAN_REFERENCE" in scopes: position_status = "HUMAN_HELD"
            elif "AI_SIMULATION" in scopes: position_status = "AI_SIMULATION_HELD"
            elif "HUMAN_REFERENCE" not in confirmed_scopes: position_status = "POSITION_DATA_MISSING"
            else: position_status = "SELECTED_NOT_HELD"
            items.append({"stock_code": code, "origins": sorted(origins[code]), "position_status": position_status, "positions": positions.get(code, []), "position_data_complete": position_status != "POSITION_DATA_MISSING"})
        maximum = int(self.config.get("fast", {}).get("max_candidates", 100))
        items = items[:maximum]
        return {
            "items": items, "pipeline_run_id": resolved.get("pipeline_run_id"),
            "source_trade_date": resolved.get("source_trade_date"), "pool_version": resolved.get("pool_version"),
            "final_candidates": resolved.get("final_count", 0),
            "manual_selected": resolved.get("manual_count", 0),
            "human_held": sum(item["position_status"] in {"HUMAN_HELD", "BOTH_HELD"} for item in items),
            "ai_held": sum(item["position_status"] in {"AI_SIMULATION_HELD", "BOTH_HELD"} for item in items),
            "active_order_plan_count": resolved.get("active_order_plan_count", 0),
            "raw_union_count": resolved.get("raw_union_count", 0),
            "deduplicated_total": len(items),
            "duplicate_count": resolved.get("duplicate_count", 0),
            "source_distribution": resolved.get("source_distribution", {}),
            "pool_hash": resolved.get("pool_hash"),
            "invariant_status": resolved.get("invariant_status"),
            "held_total": sum(_is_held_status(item["position_status"]) for item in items),
            "non_held_total": sum(item["position_status"] == "SELECTED_NOT_HELD" for item in items),
            "missing_position_data": sum(item["position_status"] == "POSITION_DATA_MISSING" for item in items),
            "position_snapshot_version": _position_version(current_positions),
        }

    def status(
        self,
        run_id: str | None = None,
        *,
        trade_date: date | None = None,
        cache_status: str = "UNKNOWN",
    ) -> dict[str, Any]:
        query = select(PostCloseActionRun)
        if run_id:
            query = query.where(PostCloseActionRun.run_id == run_id)
        elif trade_date:
            query = query.where(PostCloseActionRun.trade_date == trade_date)
        run = self.session.scalar(query.order_by(PostCloseActionRun.created_at.desc()))
        if run is None:
            return {
                "status": "NOT_RUN",
                "trade_date": trade_date,
                "cache_status": cache_status,
            }
        rows = list(self.session.scalars(select(PostCloseActionResult).where(PostCloseActionResult.run_id == run.run_id)))
        legacy_position_gate_invalid = bool(rows) and all(row.position_status == "POSITION_DATA_MISSING" for row in rows) and run.config_snapshot_json.get("action_rule_version") != "post_close_action_v1_2"
        effective_status = "LEGACY_RESULT_POSITION_GATE_INVALID" if legacy_position_gate_invalid else run.status
        actions = Counter(row.current_adopted_action for row in rows)
        held_actions = Counter(row.current_adopted_action for row in rows if _is_held_status(row.position_status))
        non_held_actions = Counter(row.current_adopted_action for row in rows if row.position_status == "SELECTED_NOT_HELD")
        return {
            "run_id": run.run_id, "trade_date": run.trade_date, "target_trade_date": run.target_trade_date,
            "run_mode": run.run_mode, "status": effective_status, "fast_rule_status": effective_status,
            "pro_review_status": run.pro_review_status, "stock_count": run.stock_count,
            "held_count": run.held_count, "non_held_count": run.non_held_count,
            "rule_duration_ms": run.rule_duration_ms, "scoring_profile": run.scoring_profile,
            "ifind_mode": "SHADOW", "action_distribution": dict(actions),
            "held_action_distribution": dict(held_actions), "non_held_action_distribution": dict(non_held_actions),
            "manual_review_count": sum(row.requires_manual_review for row in rows),
            "cache_status": cache_status, "advisory_only": True, "orders_created": 0,
            "pool": run.config_snapshot_json.get("pool", {}),
            "tiered_coverage": run.config_snapshot_json.get("tiered_coverage", {}),
            "legacy_position_gate_invalid": legacy_position_gate_invalid,
        }

    def results(self, run_id: str, *, held_only: bool | None = None, action: str | None = None, source: str | None = None, requires_manual_review: bool | None = None, page: int = 1, page_size: int = 50, sort_by: str = "stock_code", sort_order: str = "asc") -> dict[str, Any]:
        rows = list(self.session.scalars(select(PostCloseActionResult).where(PostCloseActionResult.run_id == run_id)))
        if held_only is True: rows = [row for row in rows if _is_held_status(row.position_status)]
        if held_only is False: rows = [row for row in rows if row.position_status == "SELECTED_NOT_HELD"]
        if action: rows = [row for row in rows if row.current_adopted_action == action]
        if source: rows = [row for row in rows if source in row.selection_source.split("|")]
        if requires_manual_review is not None: rows = [row for row in rows if row.requires_manual_review is requires_manual_review]
        allowed = {"stock_code", "base_rank", "enhanced_rank", "action_health_score", "current_adopted_action"}
        key = sort_by if sort_by in allowed else "stock_code"
        rows.sort(key=lambda row: (getattr(row, key) is None, getattr(row, key)), reverse=sort_order.lower() == "desc")
        total = len(rows); start = (max(1, page) - 1) * max(1, page_size)
        return {"items": [_result_dict(row) for row in rows[start:start + page_size]], "total": total, "page": page, "page_size": page_size}

    def history(self, trade_date: date | None = None) -> list[dict[str, Any]]:
        query = select(PostCloseActionRun)
        if trade_date: query = query.where(PostCloseActionRun.trade_date == trade_date)
        return [self.status(row.run_id) for row in self.session.scalars(query.order_by(PostCloseActionRun.created_at.desc()).limit(100))]

    def compare(self, run_id: str) -> dict[str, Any]:
        rows = list(self.session.scalars(select(PostCloseActionResult).where(PostCloseActionResult.run_id == run_id)))
        same = sum(row.baseline_rule_action == row.ifind_shadow_action for row in rows)
        different = len(rows) - same
        return {
            "run_id": run_id, "same_action_count": same, "different_action_count": different,
            "more_conservative_shadow_count": sum(_action_severity(row.ifind_shadow_action) > _action_severity(row.baseline_rule_action) for row in rows),
            "less_conservative_shadow_count": sum(_action_severity(row.ifind_shadow_action) < _action_severity(row.baseline_rule_action) for row in rows),
            "manual_review_divergences": sum(row.baseline_rule_action != row.ifind_shadow_action and row.requires_manual_review for row in rows),
            "items": [{"stock_code": row.stock_code, "baseline_rule_action": row.baseline_rule_action, "ifind_shadow_action": row.ifind_shadow_action, "current_adopted_action": row.current_adopted_action} for row in rows],
        }

    def compare_fast_final(self, trade_date: date) -> dict[str, Any]:
        success = {"SUCCESS", "FAST_PRELIMINARY_SUCCESS", "FAST_PARTIAL_COVERAGE", "FINAL_MODEL_VALIDATION_SUCCESS", "FINAL_PARTIAL_SUCCESS"}
        fast = self.session.scalar(select(PostCloseActionRun).where(
            PostCloseActionRun.trade_date == trade_date, PostCloseActionRun.run_mode == "POST_CLOSE_FAST",
            PostCloseActionRun.status.in_(success),
        ).order_by(PostCloseActionRun.created_at.desc()))
        final = self.session.scalar(select(PostCloseActionRun).where(
            PostCloseActionRun.trade_date == trade_date, PostCloseActionRun.run_mode == "POST_CLOSE_FINAL",
            PostCloseActionRun.status.in_(success),
        ).order_by(PostCloseActionRun.created_at.desc()))
        if not fast or not final:
            return {"status": "NOT_COMPARABLE", "trade_date": trade_date, "reason": "FAST_OR_FINAL_RUN_MISSING", "items": []}
        fast_rows = {row.stock_code: row for row in self.session.scalars(select(PostCloseActionResult).where(PostCloseActionResult.run_id == fast.run_id))}
        final_rows = {row.stock_code: row for row in self.session.scalars(select(PostCloseActionResult).where(PostCloseActionResult.run_id == final.run_id))}
        items = []
        counts = Counter()
        for code in sorted(set(fast_rows) | set(final_rows)):
            before, after = fast_rows.get(code), final_rows.get(code)
            if before is None: direction = "NEW_STOCK"
            elif after is None: direction = "REMOVED_STOCK"
            else:
                delta = _action_severity(after.current_adopted_action) - _action_severity(before.current_adopted_action)
                direction = "UNCHANGED" if before.current_adopted_action == after.current_adopted_action else "MORE_CONSERVATIVE" if delta > 0 else "LESS_CONSERVATIVE"
            counts[direction] += 1
            items.append({
                "stock_code": code, "fast_action": before.current_adopted_action if before else None,
                "final_action": after.current_adopted_action if after else None, "action_changed": direction != "UNCHANGED",
                "change_direction": direction, "change_reason": "POOL_MEMBERSHIP_CHANGED" if direction in {"NEW_STOCK", "REMOVED_STOCK"} else "FINAL_DATA_RECONCILIATION",
                "fast_score": _number(before.action_health_score) if before else None, "final_score": _number(after.action_health_score) if after else None,
                "fast_data_scope": fast.config_snapshot_json.get("tiered_coverage", {}).get("feature_scope", "PARTIAL_SNAPSHOT_ONLY"),
                "final_data_scope": final.config_snapshot_json.get("tiered_coverage", {}).get("feature_scope", "PARTIAL_SNAPSHOT_ONLY"),
                "requires_manual_review": bool(after and (after.requires_manual_review or direction == "LESS_CONSERVATIVE")),
            })
        common = len(set(fast_rows) & set(final_rows))
        return {
            "status": "COMPARABLE", "trade_date": trade_date, "fast_run_id": fast.run_id, "final_run_id": final.run_id,
            "common_stocks": common, "unchanged": counts["UNCHANGED"], "more_conservative": counts["MORE_CONSERVATIVE"],
            "less_conservative": counts["LESS_CONSERVATIVE"], "new_in_final": counts["NEW_STOCK"],
            "removed_from_final": counts["REMOVED_STOCK"],
            "manual_review_changes": sum(item["requires_manual_review"] and item["action_changed"] for item in items), "items": items,
        }

    def apply_pro_reviews(self, run_id: str, reviews: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        review_items = list(reviews)
        run = self.session.scalar(select(PostCloseActionRun).where(PostCloseActionRun.run_id == run_id))
        if run is None: raise ValueError("POST_CLOSE_ACTION_RUN_NOT_FOUND")
        rows = {row.stock_code: row for row in self.session.scalars(select(PostCloseActionResult).where(PostCloseActionResult.run_id == run_id))}
        changed = 0
        for review in review_items:
            code = normalize_ts_code(str(review.get("stock_code") or "")); row = rows.get(code)
            if row is None: continue
            review_action = str(review.get("review_action") or "MANUAL_REVIEW")
            reviewed = str(review.get("reviewed_action") or row.baseline_rule_action)
            if review_action == "MANUAL_REVIEW": reviewed = "MANUAL_REVIEW"
            adopted = enforce_conservative_pro_review(row.baseline_rule_action, reviewed, held=_is_held_status(row.position_status))
            row.pro_review_action = adopted
            row.current_adopted_action = adopted
            row.review_reason = str(review.get("reason") or "")[:1000]
            row.requires_manual_review = row.requires_manual_review or adopted == "MANUAL_REVIEW"
            changed += adopted != row.baseline_rule_action
        run.pro_review_status = "PRO_REVIEWED"
        self.session.commit()
        return {"run_id": run_id, "status": "PRO_REVIEWED", "reviewed_count": len(review_items), "changed_count": changed}

    def _inputs(self, trade_date: date, pool: dict[str, Any], quant_run: QuantRun | None, enhancement: IFindEnhancementRun | None, coverage: dict[str, Any]) -> dict[str, Any]:
        codes = [item["stock_code"] for item in pool["items"]]
        source_date = quant_run.base_market_trade_date if quant_run else trade_date
        quant = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(select(QuantRankResult).where(QuantRankResult.quant_run_id == quant_run.run_id))} if quant_run else {}
        enhanced = {row.stock_code: row for row in self.session.scalars(select(IFindStockEnhancementScore).where(IFindStockEnhancementScore.run_id == enhancement.run_id))} if enhancement else {}
        validation = self.session.scalar(select(ModelValidationRun).where(ModelValidationRun.base_market_trade_date == source_date).order_by(ModelValidationRun.created_at.desc()))
        samples = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(select(ModelValidationSample).where(ModelValidationSample.validation_run_id == validation.run_id))} if validation else {}
        pro_run = self.session.scalar(select(ProResumeRun).where(ProResumeRun.base_trade_date == source_date, ProResumeRun.status.in_(["COMPLETED", "PARTIAL_PRO_FAILURE"])).order_by(ProResumeRun.created_at.desc()))
        reviews = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(select(ProCandidateReview).where(ProCandidateReview.pro_resume_run_id == pro_run.run_id))} if pro_run else {}
        plans = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(select(ModelValidationOrderPlan).where(ModelValidationOrderPlan.validation_run_id == validation.run_id))} if validation else {}
        names = {normalize_ts_code(row.code): row.name for row in self.session.scalars(select(StockMaster).where(StockMaster.code.in_(codes)))}
        start = datetime.combine(trade_date, clock_time.min, tzinfo=SHANGHAI)
        end = start + timedelta(days=1)
        snapshots: dict[str, MarketSnapshotShadow] = {}
        for row in self.session.scalars(select(MarketSnapshotShadow).where(
            MarketSnapshotShadow.stock_code.in_(codes),
            MarketSnapshotShadow.snapshot_time >= start,
            MarketSnapshotShadow.snapshot_time < end,
            MarketSnapshotShadow.provider == "IFIND_HTTP",
        ).order_by(MarketSnapshotShadow.snapshot_time.desc())):
            snapshots.setdefault(normalize_ts_code(row.stock_code), row)
        coverage_rows = {
            row["stock_code"]: dict(row)
            for row in coverage.get("items", [])
        }
        for row in coverage.get("ranking", []):
            coverage_rows.setdefault(row["stock_code"], {}).update(row)
        return {"quant": quant, "enhanced": enhanced, "samples": samples, "reviews": reviews, "plans": plans, "names": names, "daily": _daily_cache(self.app_config.root_dir, trade_date), "snapshots": snapshots, "coverage": coverage_rows}

    def _decide(self, run_id: str, trade_date: date, target_date: date, item: dict[str, Any], data: dict[str, Any]) -> PostCloseActionResult:
        code = item["stock_code"]; quant = data["quant"].get(code); enhanced = data["enhanced"].get(code); sample = data["samples"].get(code); review = data["reviews"].get(code); plan = data["plans"].get(code); daily = data["daily"].get(code, {}); snapshot = data["snapshots"].get(code); coverage = data["coverage"].get(code, {})
        positions: list[TraderPositionSnapshot] = item["positions"]
        held = bool(positions); quantity = sum(row.quantity for row in positions) if held else None; available = sum(row.available_quantity for row in positions) if held else None
        bought_today = sum(row.quantity for row in positions if row.buy_date == trade_date) if held else 0
        sellable = target_day_sellable(quantity or 0, available or 0, bought_today, target_is_next_session=True) if held else None
        cost = _weighted_cost(positions); close = _float(daily.get("close"))
        if close is None and snapshot is not None:
            close = _float(snapshot.latest)
        closing = _closing_position(daily)
        if closing is None and snapshot is not None:
            closing = _closing_position({"close": snapshot.latest, "high": snapshot.high, "low": snapshot.low})
        base_input = ActionHealthInput(
            base_quant_score=_float(quant.total_score) if quant else None,
            flash_score=_float((sample.screening_result or {}).get("llm_score")) if sample else None,
            pro_score=_float(review.pro_score) if review else None,
            risk_health_score=_float(quant.risk_score) if quant else None,
            closing_structure_score=closing,
            relative_strength_score=50.0,
            regime_fit_score=50.0,
            position_state_score=_position_state(close, cost),
        )
        components = coverage.get("component_scores", {})
        shadow_input = ActionHealthInput(**{**base_input.__dict__, "closing_structure_score": _float(enhanced.close_quality_score) if enhanced and enhanced.close_quality_score is not None else _float(components.get("close_quality")) if components else closing, "relative_strength_score": _float(enhanced.relative_strength_score) if enhanced and enhanced.relative_strength_score is not None else _float(components.get("relative_strength")) if components else 50.0, "regime_fit_score": _float(enhanced.regime_fit_score) if enhanced and enhanced.regime_fit_score is not None else _float(components.get("market_regime_fit")) if components else 50.0})
        position = PositionFacts(held=held, quantity=quantity, available_quantity=available, target_day_sellable_quantity=sellable, bought_today_quantity=bought_today, position_data_complete=bool(item.get("position_data_complete")) and (not held or bool(cost and quantity is not None)))
        conflict = bool((enhanced and enhanced.dual_source_status == "MATERIAL_CONFLICT") or coverage.get("feature_scope") == "DATA_CONFLICTED")
        hard = HardGateInput(material_conflict=conflict, critical_data_missing=not position.position_data_complete, stop_breached=bool(close is not None and plan and plan.stop_loss_price is not None and close < float(plan.stop_loss_price)))
        baseline = self.engine.decide(base_input, position, hard)
        shadow = self.engine.decide(shadow_input, position, hard)
        feature_scope = str(coverage.get("feature_scope") or "BASELINE_ONLY")
        shadow_action = shadow.action if feature_scope == "FULL_MINUTE_OVERLAY" else "DATA_INSUFFICIENT"
        shadow_requires_review = held and feature_scope != "FULL_MINUTE_OVERLAY"
        return PostCloseActionResult(
            run_id=run_id, stock_code=code, stock_name=data["names"].get(code) or (sample.stock_name if sample else None),
            selection_source="|".join(item["origins"]), position_status=item["position_status"],
            account_scope="BOTH" if len({row.account_scope for row in positions}) > 1 else positions[0].account_scope if positions else None,
            quantity=quantity, available_quantity=available, target_day_sellable_quantity=sellable, cost_price=cost, close_price=close,
            unrealized_return=(close / cost - 1) if close is not None and cost else None,
            holding_days=max((trade_date - row.buy_date).days for row in positions if row.buy_date) if any(row.buy_date for row in positions) else None,
            base_score=quant.total_score if quant else None, ifind_shadow_score=enhanced.ifind_eod_score if enhanced else None,
            enhanced_shadow_score=enhanced.enhanced_score if enhanced else coverage.get("full_enhanced_score"), base_rank=quant.rank if quant else None,
            enhanced_rank=enhanced.enhanced_rank if enhanced else coverage.get("full_action_pool_rank"), action_health_score=baseline.action_health_score,
            baseline_rule_action=baseline.action, ifind_shadow_action=shadow_action, pro_review_action=None,
            current_adopted_action=baseline.action, current_position_percent=_first(positions, "position_percent"),
            suggested_target_position_percent=None, suggested_reduce_percent=baseline.suggested_reduce_percent,
            suggested_reduce_quantity=baseline.suggested_reduce_quantity,
            stop_loss_price=getattr(plan, "stop_loss_price", None), take_profit_1=getattr(plan, "take_profit_1_price", None),
            take_profit_2=getattr(plan, "take_profit_2_price", None), hard_gate_status=baseline.hard_gate_status,
            key_reasons_json=baseline.reasons, key_risks_json=baseline.risks,
            data_quality_status="MATERIAL_CONFLICT" if conflict else str(coverage.get("feature_scope") or ("PASS" if enhanced and enhanced.fallback_reason is None else "BASELINE_ONLY")),
            requires_manual_review=baseline.requires_manual_review or shadow_requires_review, advice_version=str(self.config.get("action_rule_version", "post_close_action_v1")),
        )

    def _action_input_hash(
        self,
        trade_date: date,
        mode: str,
        pool: dict[str, Any],
        quant: QuantRun | None,
        enhancement: IFindEnhancementRun | None,
        coverage: dict[str, Any],
        position_truth: dict[str, Any],
    ) -> str:
        codes = [item["stock_code"] for item in pool["items"]]
        payload = {
            "trade_date": trade_date.isoformat(),
            "target_trade_date": _next_trade_date_from_cache(Path(self.app_config.root_dir), trade_date).isoformat(),
            "mode": mode,
            "quant_run_id": quant.run_id if quant else None,
            "enhancement_run_id": enhancement.run_id if enhancement else None,
            "candidate_codes": codes,
            "candidate_set_hash": pool.get("pool_hash"),
            "position_version": pool.get("position_snapshot_version"),
            "position_snapshot_hash": self._position_truth_hash(position_truth.get("required_scopes", [])),
            "ifind_market_hash": self._ifind_market_hash(trade_date, codes),
            "tushare_watermark_hash": self._tushare_watermark_hash(trade_date),
            "coverage": coverage,
            "config": self._safe_config(),
            "scoring_profile": ScoringProfile.TUSHARE_BASELINE_V1,
            "pro_prompt_version": self.config.get("pro_review", {}).get("prompt_version"),
        }
        raw = json.dumps(_json_safe(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def _position_truth_hash(self, scopes: list[str]) -> str:
        values = list(self.session.scalars(select(PositionTruthConfirmation.snapshot_hash).where(
            PositionTruthConfirmation.account_scope.in_(scopes),
            PositionTruthConfirmation.is_current.is_(True),
        ))) if scopes else []
        return hashlib.sha256("|".join(sorted(values)).encode()).hexdigest()

    def _ifind_market_hash(self, trade_date: date, codes: list[str]) -> str:
        start = datetime.combine(trade_date, clock_time.min, tzinfo=SHANGHAI)
        end = start + timedelta(days=1)
        snapshots = self.session.scalars(select(MarketSnapshotShadow).where(
            MarketSnapshotShadow.stock_code.in_(codes),
            MarketSnapshotShadow.snapshot_time >= start,
            MarketSnapshotShadow.snapshot_time < end,
            MarketSnapshotShadow.provider == "IFIND_HTTP",
        ).order_by(MarketSnapshotShadow.stock_code, MarketSnapshotShadow.snapshot_time)).all() if codes else []
        minutes = self.session.scalars(select(MarketMinuteBarShadow).where(
            MarketMinuteBarShadow.stock_code.in_(codes),
            MarketMinuteBarShadow.bar_time >= start,
            MarketMinuteBarShadow.bar_time < end,
            MarketMinuteBarShadow.provider == "IFIND_HTTP",
        ).order_by(MarketMinuteBarShadow.stock_code, MarketMinuteBarShadow.bar_time)).all() if codes else []
        payload = {
            "snapshots": [(row.stock_code, row.snapshot_time, row.response_hash) for row in snapshots],
            "minutes": [(row.stock_code, row.bar_time, str(row.close), str(row.volume)) for row in minutes],
        }
        return hashlib.sha256(json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _tushare_watermark_hash(self, trade_date: date) -> str:
        root = Path(self.app_config.root_dir) / "data" / "cache" / "tushare" / "trade_date"
        digest = hashlib.sha256()
        for name in ("daily", "daily_basic", "moneyflow", "stk_limit", "adj_factor"):
            path = root / name / f"{trade_date:%Y%m%d}.json"
            digest.update(name.encode())
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"MISSING")
        return digest.hexdigest()

    def _safe_config(self) -> dict[str, Any]:
        root = self.app_config.config_files.get("post_close_action", {})
        return {"default_scoring_profile": ScoringProfile.TUSHARE_BASELINE_V1, "action_rule_version": self.config.get("action_rule_version"), "thresholds": self.config.get("thresholds", {}), "candidate_thresholds": self.config.get("candidate_thresholds", {}), "reduce": self.config.get("reduce", {}), "position_truth": root.get("position_truth", {}), "real_trading_enabled": False, "scheduler_enabled": False}

    def _daily_ready(self, trade_date: date) -> bool:
        return self._tushare_final_readiness(trade_date)["ready"]

    def _tushare_final_readiness(self, trade_date: date) -> dict[str, Any]:
        required = ("daily", "daily_basic", "moneyflow", "stk_limit", "adj_factor")
        cache_root = Path(self.app_config.root_dir) / "data" / "cache" / "tushare"
        watermark_service = DatasetWatermarkService(cache_root=cache_root)
        datasets: dict[str, dict[str, Any]] = {}
        for name in required:
            watermark = watermark_service.trade_date_watermark(name, trade_date)
            datasets[name] = {
                "ready": bool(watermark.is_complete and watermark.latest_trade_date == trade_date),
                "row_count": watermark.row_count,
                "expected_count": watermark.expected_count,
                "coverage_ratio": watermark.coverage_ratio,
                "latest_trade_date": watermark.latest_trade_date,
            }
        missing = [name for name, value in datasets.items() if not value["ready"]]
        return {
            "ready": not missing,
            "trade_date": trade_date,
            "base_trade_date": trade_date,
            "datasets": datasets,
            "missing": missing,
            "temporal_status": "PASS" if not missing else "BLOCKED",
            "actionable": not missing,
        }

    def _ensure_fast_ifind_data(self, trade_date: date, pool: dict[str, Any]) -> dict[str, Any]:
        pool_codes = [item["stock_code"] for item in pool.get("items", [])]
        if self._ifind_ready(trade_date, pool_codes):
            return {"status": "CACHE_HIT", "auth_calls": 0, "index_calls": 0, "snapshot_calls": 0, "minute_calls": 0, "retries": 0, "cache_hits": 1, "total": 0, "hard_limit": int(self.config.get("fast", {}).get("maximum_external_calls", 40))}
        maximum_calls = int(self.config.get("fast", {}).get("maximum_external_calls", 40))
        priority = {"HUMAN_HELD": 0, "AI_HELD": 1, "ACTIVE_ORDER_PLAN": 2, "MANUAL": 3, "FINAL": 4}
        ordered = sorted(
            pool.get("items", []),
            key=lambda item: (
                min((priority.get(origin, 99) for origin in item.get("origins", [])), default=99),
                item["stock_code"],
            ),
        )
        selected = ordered[:20]
        fetch_pool = {
            **pool,
            "items": selected,
            "stock_count": len(selected),
            "deduplicated_count": len(selected),
            "pool_hash": hashlib.sha256("|".join(item["stock_code"] for item in selected).encode()).hexdigest(),
        }
        minute_count = min(int(self.config.get("fast", {}).get("maximum_minute_stocks", 20)), len(selected))
        report = IFindShadowAcceptanceService(self.session, app_config=self.app_config).run(
            mode="closed-session",
            trade_date=trade_date,
            pipeline_run_id=pool.get("pipeline_run_id"),
            stock_limit=len(selected),
            minute_stock_count=minute_count,
            max_external_calls=maximum_calls,
            force_provider_refresh=False,
            pool_override=fetch_pool,
        )
        accepted = report.get("status") not in {"BLOCKED", "ACCEPTANCE_FAILED", "CALL_LIMIT_REACHED"}
        index_requested = int(report.get("index_requested", 0))
        stock_requested = int(report.get("stock_requested", 0))
        minute_requested = int(report.get("minute_stock_count", 0))
        return {
            "status": report.get("status"),
            "acceptance_run_id": report.get("acceptance_run_id"),
            "auth_calls": int(report.get("auth_call_count", 0)),
            "index_calls": 2 * ((index_requested + 1) // 2) if accepted else 0,
            "snapshot_calls": (stock_requested + 3) // 4 if accepted else 0,
            "minute_calls": minute_requested if accepted else 0,
            "retries": 0,
            "cache_hits": int(report.get("cache_hit_count", 0)),
            "total": int(report.get("external_call_count", 0)) + int(report.get("auth_call_count", 0)),
            "hard_limit": maximum_calls,
        }

    def _ifind_ready(self, trade_date: date, stock_codes: list[str] | None = None) -> bool:
        start = datetime.combine(trade_date, clock_time.min, tzinfo=SHANGHAI)
        end = start + timedelta(days=1)
        conditions = [
            MarketSnapshotShadow.provider == "IFIND_HTTP",
            MarketSnapshotShadow.snapshot_time >= start,
            MarketSnapshotShadow.snapshot_time < end,
            MarketSnapshotShadow.data_status == "CLOSED_SESSION_FINAL",
        ]
        if stock_codes:
            conditions.append(MarketSnapshotShadow.stock_code.in_(stock_codes))
        return bool(self.session.scalar(select(MarketSnapshotShadow.id).where(*conditions).limit(1)))


def _dual_status(snapshot: MarketSnapshotShadow | None, official: dict[str, Any] | None) -> str:
    if snapshot is None or official is None or snapshot.latest is None or official.get("close") is None:
        return "SOURCE_MISSING"
    actual, expected = float(snapshot.latest), float(official["close"])
    tolerance = max(0.01, abs(expected) * 0.0005)
    return "MATCH_WITH_TOLERANCE" if abs(actual - expected) <= tolerance else "MATERIAL_CONFLICT"


def _market_session(local: datetime, trade_date: date) -> str:
    if local.date() != trade_date:
        return "CLOSED" if local.date() > trade_date else "PRE_MARKET"
    value = local.time()
    if value < clock_time(9, 15):
        return "PRE_MARKET"
    if value < clock_time(9, 30):
        return "OPENING_AUCTION"
    if value <= clock_time(11, 30):
        return "MORNING_SESSION"
    if value < clock_time(13, 0):
        return "MIDDAY_BREAK"
    if value < clock_time(15, 1):
        return "AFTERNOON_SESSION"
    return "POST_MARKET"


def _daily_cache(root: Path, trade_date: date) -> dict[str, dict[str, Any]]:
    path = Path(root) / "data" / "cache" / "tushare" / "trade_date" / "daily" / f"{trade_date:%Y%m%d}.json"
    try: values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError): return {}
    return {normalize_ts_code(row["ts_code"]): row for row in values if isinstance(row, dict) and row.get("ts_code")}


def _result_dict(row: PostCloseActionResult) -> dict[str, Any]:
    return {column.name: _json_value(getattr(row, column.name)) for column in row.__table__.columns}


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, Decimal): return float(value)
    return value


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, default=_json_value))


def _number(value: Any) -> float | None:
    return _float(value)


def _float(value: Any) -> float | None:
    try: return None if value is None else float(value)
    except (TypeError, ValueError): return None


def _return(close: float, previous: float) -> float:
    return close / previous - 1 if previous else 0.0


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


def _spearman(rows: list[IFindStockEnhancementScore]) -> float | None:
    count = len(rows)
    if count < 2: return None
    squared = sum((row.base_rank - row.enhanced_rank) ** 2 for row in rows)
    return round(1 - 6 * squared / (count * (count * count - 1)), 8)


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values: return {"count": 0, "minimum": None, "median": None, "maximum": None}
    return {"count": len(values), "minimum": min(values), "median": median(values), "maximum": max(values)}


def _percentile(values: list[float], percentile: float) -> float:
    if not values: return 0.0
    ordered = sorted(values); position = (len(ordered) - 1) * percentile; lower = int(position); upper = min(lower + 1, len(ordered) - 1); weight = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight, 4)


def _position_version(rows: list[TraderPositionSnapshot]) -> str | None:
    versions = sorted({row.version for row in rows})
    return hashlib.sha256("|".join(versions).encode()).hexdigest()[:24] if versions else None


def _next_trade_date_from_cache(root_dir: Path, day: date) -> date:
    open_dates: set[date] = set()
    cache_dir = root_dir / "data" / "cache" / "tushare"
    for path in cache_dir.glob("trade_cal_*.json"):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or int(row.get("is_open") or 0) != 1:
                continue
            value = str(row.get("cal_date") or "")
            try:
                open_dates.add(datetime.strptime(value, "%Y%m%d").date())
            except ValueError:
                continue
    future_dates = sorted(value for value in open_dates if value > day)
    if future_dates:
        return TradeCalendarService(open_dates=future_dates).next_open_trade_date(day)

    # Offline fallback is used only when the audited calendar cache is absent.
    value = day + timedelta(days=1)
    while value.weekday() >= 5: value += timedelta(days=1)
    return value


def _weighted_cost(rows: list[TraderPositionSnapshot]) -> float | None:
    total = sum(row.quantity for row in rows)
    return sum(float(row.cost_price) * row.quantity for row in rows) / total if total else None


def _position_state(close: float | None, cost: float | None) -> float | None:
    if close is None or cost is None or cost <= 0: return None
    return max(0.0, min(100.0, 50 + (close / cost - 1) * 250))


def _closing_position(row: Mapping[str, Any]) -> float | None:
    close, high, low = (_float(row.get(name)) for name in ("close", "high", "low"))
    if close is None or high is None or low is None: return None
    return 50.0 if high == low else max(0.0, min(100.0, (close - low) / (high - low) * 100))


def _first(rows: list[Any], name: str) -> Any:
    return getattr(rows[0], name) if rows else None


def _action_severity(value: str) -> int:
    values = {"CONTINUE_HOLD": 0, "PREPARE_ENTRY": 0, "KEEP_WATCH": 1, "HOLD_WITH_TIGHT_STOP": 1, "DO_NOT_CHASE": 2, "REDUCE_POSITION": 2, "REMOVE_FROM_POOL": 3, "EXIT_NEXT_SESSION": 3, "T_PLUS_ONE_LOCKED_EXIT_PLAN": 3, "EXIT_WHEN_TRADABLE": 4, "MANUAL_REVIEW": 5, "DATA_INSUFFICIENT": 5}
    return values.get(value, 5)


def _is_held_status(value: str) -> bool:
    return value in {"HUMAN_HELD", "AI_SIMULATION_HELD", "BOTH_HELD"}
