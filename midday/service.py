from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import date, datetime, time as clock_time, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import func, select

from backend.core.config import AppConfig, get_app_config
from database.models import (
    MiddayRecommendationResult,
    MiddayRecommendationRun,
    MiddayRecheckResult,
    MarketMinuteBarShadow,
    MarketSnapshotShadow,
    StockMaster,
    TraderPositionSnapshot,
    LLMUsage,
)
from midday.core import MiddayBaselineResolver, MiddayPoolResolver, MiddayTimeGate, SHANGHAI, distribution
from midday.llm_review import MiddayLLMReviewer
from midday.provider import MiddayIFindCollector
from midday.scoring import MiddayScore, MiddayScoringEngine
from post_close.service import PostCloseActionService
from stock_codes import normalize_ts_code


class MiddayRecommendationService:
    def __init__(
        self,
        session,
        app_config: AppConfig | None = None,
        *,
        collector: MiddayIFindCollector | None = None,
        reviewer: MiddayLLMReviewer | None = None,
    ) -> None:
        self.session = session
        self.app_config = app_config or get_app_config()
        self.config = self.app_config.config_files.get("midday_recommendation", {}).get("midday_recommendation", {})
        self.collector = collector
        self.reviewer = reviewer
        self.scoring = MiddayScoringEngine(self.config)

    def run(
        self,
        trade_date: date,
        *,
        decision_time: datetime | None = None,
        allow_real_external: bool = True,
        historical_validation: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        now = (decision_time or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        gate = MiddayTimeGate(
            start_after=str(self.config.get("start_after", "11:32")),
            latest=str(self.config.get("latest_start_time", "12:50")),
        ).evaluate(trade_date, now)
        if not gate["passed"] and not historical_validation:
            return self._persist_stopped(trade_date, now, gate, started)
        if historical_validation:
            gate = {**gate, "passed": True, "status": "HISTORICAL_MIDDAY_VALIDATION", "market_session": "HISTORICAL_MIDDAY_VALIDATION"}
        if not bool(self.config.get("enabled", False)):
            return self._persist_stopped(trade_date, now, {**gate, "status": "MIDDAY_PIPELINE_DISABLED"}, started)
        if self.app_config.real_trading_enabled:
            return self._persist_stopped(trade_date, now, {**gate, "status": "BLOCKED_REAL_TRADING_ENABLED"}, started)

        baseline = MiddayBaselineResolver(self.session).resolve(
            trade_date, top_n=int(self.config.get("pool", {}).get("base_top_n", 100)),
        )
        truth = PostCloseActionService(self.session, self.app_config).position_truth(trade_date, now=now)
        pool = MiddayPoolResolver(self.session).resolve(
            trade_date,
            baseline,
            maximum=int(self.config.get("pool", {}).get("maximum_size", 120)),
            truth=truth,
        )
        input_hash = self._input_hash(trade_date, baseline, pool)
        existing = self.session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.input_hash == input_hash))
        if existing and existing.status in {"SUCCESS", "PARTIAL_SUCCESS", "WAITING_AFTERNOON_RECHECK"} and not force:
            return self.status(existing.run_id)
        if existing:
            result_count = int(self.session.scalar(select(func.count()).select_from(MiddayRecommendationResult).where(
                MiddayRecommendationResult.run_id == existing.run_id,
            )) or 0)
            if result_count:
                reviewer = self.reviewer or MiddayLLMReviewer(self.session, self.config)
                if reviewer.gate()["passed"]:
                    return self.resume_llm(existing.run_id)
                return self.status(existing.run_id)
            run = existing
            run.status = "RUNNING"
            run.current_stage = "PREFLIGHT_RETRY"
            run.completed_at = None
            run.snapshot_count = 0
            run.minute_count = 0
            run.flash_count = 0
            run.pro_count = 0
            run.final_count = 0
            run.token_usage = 0
            run.cost = 0
            run.checkpoint_json = {"retry_reused_input_hash": True, "orders_created": 0}
        else:
            run = self._new_run(trade_date, now, gate, input_hash, baseline=baseline, pool=pool)
            self.session.add(run)
        self.session.commit()
        if not allow_real_external:
            run.status = "BLOCKED_EXTERNAL_EXECUTION_DISABLED"
            run.current_stage = "PREFLIGHT"
            run.completed_at = datetime.now(timezone.utc)
            run.total_duration_ms = _duration_ms(started)
            self.session.commit()
            return self.status(run.run_id)

        collector = self.collector or MiddayIFindCollector(
            self.session, self.app_config, self.config.get("ifind", {}),
        )
        collector_gate = collector.gate()
        if not collector_gate["passed"]:
            run.status = "BLOCKED_IFIND_UNAVAILABLE"
            run.current_stage = "IFIND_GATE"
            run.checkpoint_json = {**run.checkpoint_json, "ifind_gate": collector_gate}
            run.completed_at = datetime.now(timezone.utc)
            run.total_duration_ms = _duration_ms(started)
            self.session.commit()
            return self.status(run.run_id)

        run.current_stage = "SNAPSHOT"
        self.session.commit()
        codes = [item["stock_code"] for item in pool["items"]]
        fast_minutes_only = bool(self.config.get("ifind", {}).get("fast_minutes_only", False))
        if historical_validation:
            snapshots = collector.load_morning_snapshots(trade_date, codes, cutoff=clock_time(11, 30))
            snapshots_payload = {"index_rows": [], "stock_rows": list(snapshots.values())}
        elif fast_minutes_only:
            snapshots = {}
            snapshots_payload = {"index_rows": [], "stock_rows": []}
        else:
            snapshots_payload = collector.collect_snapshots(trade_date, codes)
            snapshots = {normalize_ts_code(_row_code(row)): row for row in snapshots_payload["stock_rows"]}
        index_return = _median_index_return(snapshots_payload["index_rows"])
        quick = [(item, self.scoring.score(item, snapshots.get(item["stock_code"]), [], index_return=index_return)) for item in pool["items"]]
        quick.sort(key=lambda pair: (_pool_priority(pair[0]), -pair[1].enhanced_score, pair[0]["base_quant_rank"], pair[0]["stock_code"]))
        minute_limit = int(self.config.get("pool", {}).get("minute_pool_size", 30))
        minute_codes = [item["stock_code"] for item, _ in quick[:minute_limit]]
        run.current_stage = "MINUTE"
        self.session.commit()
        minutes = collector.collect_minutes(trade_date, minute_codes)
        if fast_minutes_only and not historical_validation:
            prior = collector.load_morning_snapshots(trade_date, minute_codes, cutoff=clock_time(11, 30))
            snapshots = {
                code: _minute_snapshot(rows, prior.get(code))
                for code, rows in minutes.items()
                if rows
            }
        scored = [(item, self.scoring.score(item, snapshots.get(item["stock_code"]), minutes.get(item["stock_code"], []), index_return=index_return)) for item in pool["items"]]
        scored.sort(key=lambda pair: (-pair[1].enhanced_score, pair[0]["base_quant_rank"], pair[0]["stock_code"]))

        existing_results = int(self.session.scalar(select(func.count()).select_from(MiddayRecommendationResult).where(
            MiddayRecommendationResult.run_id == run.run_id,
        )) or 0)
        if existing_results == 0:
            self._persist_results(run, scored, {}, {}, [], snapshots)
            run.current_stage = "DETERMINISTIC_CHECKPOINT"
            run.snapshot_count = len(snapshots)
            run.minute_count = sum(bool(value) for value in minutes.values())
            run.checkpoint_json = {
                **dict(run.checkpoint_json or {}),
                "deterministic_checkpoint": True,
                "fast_minutes_only": fast_minutes_only,
                "collector": collector.summary(),
                "orders_created": 0,
            }
            self.session.commit()

        flash_limit = int(self.config.get("pool", {}).get("flash_pool_size", 30))
        flash_items = scored[:flash_limit]
        reviewer = self.reviewer or MiddayLLMReviewer(self.session, self.config)
        if not reviewer.gate()["passed"]:
            run.status = "BLOCKED_REAL_LLM_UNAVAILABLE"
            run.current_stage = "FLASH_GATE"
            run.checkpoint_json = {**run.checkpoint_json, "collector": collector.summary(), "llm_gate": reviewer.gate()}
            run.snapshot_count = len(snapshots)
            run.minute_count = sum(bool(value) for value in minutes.values())
            run.completed_at = datetime.now(timezone.utc)
            run.total_duration_ms = _duration_ms(started)
            self.session.commit()
            return self.status(run.run_id)

        run.current_stage = "FLASH"
        self.session.commit()
        flash_results: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, str]] = []
        flash_results, flash_failures = reviewer.review_many(
            "FLASH", [(item["stock_code"], _llm_payload(item, score)) for item, score in flash_items], run.run_id,
        )
        failures.extend(flash_failures)
        flash_ranked = sorted(
            [(item, score, flash_results.get(item["stock_code"])) for item, score in flash_items],
            key=lambda value: (-float((value[2] or {}).get("score", value[1].enhanced_score)), value[0]["base_quant_rank"], value[0]["stock_code"]),
        )
        pro_limit = int(self.config.get("pool", {}).get("pro_pool_size", 20))
        pro_results: dict[str, dict[str, Any]] = {}
        run.current_stage = "PRO"
        self.session.commit()
        pro_candidates = [value for value in flash_ranked if (value[2] or {}).get("decision") in {"PASS", "WATCH"}][:pro_limit]
        pro_results, pro_failures = reviewer.review_many(
            "PRO", [(item["stock_code"], {**_llm_payload(item, score), "flash": flash}) for item, score, flash in pro_candidates], run.run_id,
        )
        failures.extend(pro_failures)
        final_limit = int(self.config.get("pool", {}).get("final_count", 20))
        final_codes = [item["stock_code"] for item, _, _ in sorted(
            [value for value in pro_candidates if (pro_results.get(value[0]["stock_code"]) or {}).get("decision") in {"PRIORITY", "WATCH"}],
            key=lambda value: (-float((pro_results.get(value[0]["stock_code"]) or {}).get("score", (value[2] or {}).get("score", value[1].enhanced_score))), value[0]["base_quant_rank"], value[0]["stock_code"]),
        )[:final_limit]]
        self._apply_review_results(run.run_id, flash_results, pro_results, final_codes)
        usage = reviewer.usage()
        stats = collector.summary()
        run.status = "WAITING_AFTERNOON_RECHECK" if bool(self.config.get("require_afternoon_recheck", True)) else ("PARTIAL_SUCCESS" if failures else "SUCCESS")
        run.current_stage = "WAITING_AFTERNOON_RECHECK" if run.status == "WAITING_AFTERNOON_RECHECK" else "COMPLETED"
        run.snapshot_count = len(snapshots)
        run.minute_count = sum(bool(value) for value in minutes.values())
        run.flash_count = len(flash_results)
        run.pro_count = len(pro_results)
        run.final_count = len(final_codes)
        run.held_count = sum(item["position_status"] == "HELD" for item in pool["items"])
        run.token_usage = usage["tokens"]
        run.cost = _decimal(usage["cost"])
        run.checkpoint_json = {
            "collector": stats,
            "llm": usage,
            "failure_count": len(failures),
            "failures": failures,
            "feature_scope_distribution": distribution([score.feature_scope for _, score in scored]),
            "official_quant_changed": False,
            "orders_created": 0,
            "historical_validation": historical_validation,
            "market_data_cutoff": f"{trade_date.isoformat()}T11:30:00+08:00" if historical_validation or fast_minutes_only else None,
            "deterministic_checkpoint": True,
            "fast_minutes_only": fast_minutes_only,
        }
        run.completed_at = datetime.now(timezone.utc)
        run.total_duration_ms = _duration_ms(started)
        self.session.commit()
        return self.status(run.run_id)

    def status(self, run_id: str | None = None, *, trade_date: date | None = None) -> dict[str, Any]:
        query = select(MiddayRecommendationRun)
        if run_id:
            query = query.where(MiddayRecommendationRun.run_id == run_id)
        if trade_date:
            query = query.where(MiddayRecommendationRun.session_trade_date == trade_date)
        run = self.session.scalar(query.order_by(MiddayRecommendationRun.created_at.desc()))
        return _run_dict(run) if run else {"status": "NOT_RUN", "trade_date": trade_date}

    def results(self, run_id: str, *, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        total = self.session.scalar(select(func.count()).select_from(MiddayRecommendationResult).where(MiddayRecommendationResult.run_id == run_id)) or 0
        rows = list(self.session.scalars(select(MiddayRecommendationResult).where(
            MiddayRecommendationResult.run_id == run_id,
        ).order_by(MiddayRecommendationResult.pro_rank.is_(None), MiddayRecommendationResult.pro_rank, MiddayRecommendationResult.base_quant_rank).offset((page - 1) * page_size).limit(page_size)))
        return {"items": [_result_dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def history(self, trade_date: date | None = None) -> list[dict[str, Any]]:
        query = select(MiddayRecommendationRun)
        if trade_date:
            query = query.where(MiddayRecommendationRun.session_trade_date == trade_date)
        return [_run_dict(row) for row in self.session.scalars(query.order_by(MiddayRecommendationRun.created_at.desc()).limit(100))]

    def recheck(self, run_id: str, *, now: datetime | None = None, snapshots: dict[str, Any] | None = None) -> dict[str, Any]:
        run = self.session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.run_id == run_id))
        if run is None:
            raise ValueError("MIDDAY_RUN_NOT_FOUND")
        local = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        if not (clock_time(13, 1) <= local.time().replace(tzinfo=None) <= clock_time(13, 10)):
            return {"run_id": run_id, "status": "RECHECK_WINDOW_REQUIRED", "llm_calls": 0}
        snapshots = snapshots or {}
        rows = list(self.session.scalars(select(MiddayRecommendationResult).where(MiddayRecommendationResult.run_id == run_id, MiddayRecommendationResult.pro_rank.is_not(None))))
        invalidated = 0
        for row in rows:
            quote = snapshots.get(row.stock_code)
            latest = _number(_field(quote, "latest"))
            recommended = float(row.recommended_price or 0)
            deviation = latest / recommended - 1.0 if latest and recommended else None
            status = "VALID"
            reason = None
            if latest is None:
                status, reason = "MANUAL_REVIEW", "RECHECK_SNAPSHOT_MISSING"
            elif row.max_acceptable_price is not None and latest > float(row.max_acceptable_price):
                status, reason = "INVALIDATED", "ABOVE_MAX_ACCEPTABLE_PRICE"
                invalidated += 1
            self.session.add(MiddayRecheckResult(
                midday_run_id=run_id, stock_code=row.stock_code, recheck_time=local,
                latest_price=_decimal(latest), index_status="NOT_RECHECKED", freshness="PASS" if latest else "MISSING",
                price_deviation=_decimal(deviation), hard_gate_status=row.hard_gate_status,
                recheck_status=status, invalidation_reason=reason,
            ))
        run.status = "SUCCESS" if not invalidated else "PARTIAL_SUCCESS"
        run.current_stage = "COMPLETED"
        self.session.commit()
        return {"run_id": run_id, "status": run.status, "checked": len(rows), "invalidated": invalidated, "llm_calls": 0, "orders_created": 0}

    def methodology(self) -> dict[str, Any]:
        return {
            "baseline": "上一兼容量化交易日的 Tushare Quant Top100，不重跑全A量化",
            "overlay": "午间 iFinD 快照/分钟线只形成独立影子增量，不覆盖正式 Quant",
            "scope": ["MORNING_FULL_MINUTE", "MORNING_SNAPSHOT_ONLY", "BASELINE_ONLY", "DATA_CONFLICTED"],
            "safety": {"advisory_only": True, "actionable": False, "orders_created": 0, "real_trading": False},
        }

    def resume_llm(self, run_id: str) -> dict[str, Any]:
        """Resume only Flash/Pro from persisted deterministic midday facts."""
        run = self.session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.run_id == run_id))
        if run is None:
            raise ValueError("MIDDAY_RUN_NOT_FOUND")
        rows = list(self.session.scalars(select(MiddayRecommendationResult).where(
            MiddayRecommendationResult.run_id == run_id,
        ).order_by(MiddayRecommendationResult.midday_enhanced_score.desc(), MiddayRecommendationResult.base_quant_rank)))
        if not rows:
            raise ValueError("MIDDAY_DETERMINISTIC_RESULTS_NOT_FOUND")
        reviewer = self.reviewer or MiddayLLMReviewer(self.session, self.config)
        if not reviewer.gate()["passed"]:
            raise RuntimeError("REAL_LLM_REQUIRED")
        started = time.perf_counter()
        failures: list[dict[str, str]] = []
        flash_limit = int(self.config.get("pool", {}).get("flash_pool_size", 30))
        flash_rows = rows[:flash_limit]
        flash_results: dict[str, dict[str, Any]] = {}
        run.status = "RUNNING"
        run.current_stage = "FLASH_RESUME"
        self.session.commit()
        flash_results, flash_failures = reviewer.review_many(
            "FLASH", [(row.stock_code, _persisted_llm_payload(row)) for row in flash_rows], run_id,
        )
        failures.extend(flash_failures)
        for row in flash_rows:
            payload = flash_results.get(row.stock_code)
            if payload is not None:
                row.midday_flash_score = _decimal(payload["score"])
                row.flash_decision = payload["decision"]
                row.key_reasons = list(dict.fromkeys([*row.key_reasons, *payload.get("reasons", [])]))[:8]
                row.key_risks = list(dict.fromkeys([*row.key_risks, *payload.get("risks", [])]))[:8]
        run.flash_count = len(flash_results)
        run.current_stage = "PRO_RESUME"
        self.session.commit()
        flash_ranked = sorted(
            [row for row in flash_rows if row.stock_code in flash_results],
            key=lambda row: (-float(row.midday_flash_score or 0), row.base_quant_rank, row.stock_code),
        )
        pro_limit = int(self.config.get("pool", {}).get("pro_pool_size", 20))
        run.current_stage = "PRO_RESUME"
        self.session.commit()
        pro_results: dict[str, dict[str, Any]] = {}
        pro_candidates = [row for row in flash_ranked if flash_results[row.stock_code].get("decision") in {"PASS", "WATCH"}][:pro_limit]
        pro_results, pro_failures = reviewer.review_many(
            "PRO", [(row.stock_code, {**_persisted_llm_payload(row), "flash": flash_results[row.stock_code]}) for row in pro_candidates], run_id,
        )
        failures.extend(pro_failures)
        for row in pro_candidates:
            payload = pro_results.get(row.stock_code)
            if payload is not None:
                row.midday_pro_score = _decimal(payload["score"])
                row.key_reasons = list(dict.fromkeys([*row.key_reasons, *payload.get("reasons", [])]))[:8]
                row.key_risks = list(dict.fromkeys([*row.key_risks, *payload.get("risks", [])]))[:8]
        pro_ranked = sorted(
            [row for row in pro_candidates if row.stock_code in pro_results and pro_results[row.stock_code].get("decision") in {"PRIORITY", "WATCH"}],
            key=lambda row: (-float(row.midday_pro_score or 0), -float(row.midday_flash_score or 0), row.base_quant_rank, row.stock_code),
        )
        for row in rows:
            row.pro_rank = None
            row.suggested_weight = None
            row.suggested_position_percent = None
        final_limit = int(self.config.get("pool", {}).get("final_count", 20))
        final_rows = pro_ranked[:final_limit]
        weights = _equal_weights([row.stock_code for row in final_rows])
        for index, row in enumerate(final_rows, 1):
            row.pro_rank = index
            row.suggested_weight = weights[row.stock_code]
            row.suggested_position_percent = weights[row.stock_code]
        usage = reviewer.usage()
        previous = dict(run.checkpoint_json or {})
        run.flash_count = len(flash_results)
        run.pro_count = len(pro_results)
        run.final_count = len(final_rows)
        run.token_usage = int(run.token_usage or 0) + usage["tokens"]
        run.cost = _decimal(float(run.cost or 0) + usage["cost"])
        run.status = "WAITING_AFTERNOON_RECHECK" if not failures and final_rows and run.recheck_required else ("SUCCESS" if not failures and final_rows else "PARTIAL_SUCCESS")
        run.current_stage = "WAITING_AFTERNOON_RECHECK" if run.status == "WAITING_AFTERNOON_RECHECK" else "COMPLETED"
        run.total_duration_ms = int(run.total_duration_ms or 0) + _duration_ms(started)
        run.completed_at = datetime.now(timezone.utc)
        run.checkpoint_json = {
            **previous,
            "llm": usage,
            "failure_count": len(failures),
            "failures": failures,
            "llm_resume": True,
            "orders_created": 0,
        }
        self.session.commit()
        return self.status(run_id)

    def finalize_llm_ranking(self, run_id: str) -> dict[str, Any]:
        """Apply deterministic acceptance filtering to persisted Flash/Pro outputs."""
        run = self.session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.run_id == run_id))
        if run is None:
            raise ValueError("MIDDAY_RUN_NOT_FOUND")
        rows = list(self.session.scalars(select(MiddayRecommendationResult).where(MiddayRecommendationResult.run_id == run_id)))
        flash_ranked = sorted(
            [row for row in rows if row.midday_flash_score is not None and row.flash_decision in {"PASS", "WATCH"}],
            key=lambda row: (-float(row.midday_flash_score or 0), row.base_quant_rank, row.stock_code),
        )
        pro_limit = int(self.config.get("pool", {}).get("pro_pool_size", 20))
        accepted = [row for row in flash_ranked[:pro_limit] if row.midday_pro_score is not None]
        accepted.sort(key=lambda row: (-float(row.midday_pro_score or 0), -float(row.midday_flash_score or 0), row.base_quant_rank, row.stock_code))
        final_rows = accepted[:int(self.config.get("pool", {}).get("final_count", 20))]
        for row in rows:
            row.pro_rank = None
            row.suggested_weight = None
            row.suggested_position_percent = None
        weights = _equal_weights([row.stock_code for row in final_rows])
        for index, row in enumerate(final_rows, 1):
            row.pro_rank = index
            row.suggested_weight = weights[row.stock_code]
            row.suggested_position_percent = weights[row.stock_code]
        run.final_count = len(final_rows)
        checkpoint = dict(run.checkpoint_json or {})
        checkpoint["deterministic_acceptance_filter_applied"] = True
        checkpoint["final_codes"] = [row.stock_code for row in final_rows]
        checkpoint["orders_created"] = 0
        run.checkpoint_json = checkpoint
        self.session.commit()
        return {"run_id": run_id, "final_count": len(final_rows), "final_codes": checkpoint["final_codes"], "orders_created": 0}

    def retry_missing_llm(self, run_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        run = self.session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.run_id == run_id))
        if run is None:
            raise ValueError("MIDDAY_RUN_NOT_FOUND")
        rows = list(self.session.scalars(select(MiddayRecommendationResult).where(
            MiddayRecommendationResult.run_id == run_id,
        ).order_by(MiddayRecommendationResult.midday_enhanced_score.desc(), MiddayRecommendationResult.base_quant_rank)))
        reviewer = self.reviewer or MiddayLLMReviewer(self.session, self.config)
        flash_limit = int(self.config.get("pool", {}).get("flash_pool_size", 30))
        failures: list[dict[str, str]] = []
        retried_flash = 0
        missing_flash = [value for value in rows[:flash_limit] if value.midday_flash_score is None]
        flash_payloads, flash_failures = reviewer.review_many(
            "FLASH", [(row.stock_code, _persisted_llm_payload(row)) for row in missing_flash], run_id,
        ) if missing_flash else ({}, [])
        failures.extend(flash_failures)
        for row in missing_flash:
            payload = flash_payloads.get(row.stock_code)
            if payload is not None:
                row.midday_flash_score = _decimal(payload["score"])
                row.flash_decision = payload["decision"]
                row.key_reasons = list(dict.fromkeys([*row.key_reasons, *payload.get("reasons", [])]))[:8]
                row.key_risks = list(dict.fromkeys([*row.key_risks, *payload.get("risks", [])]))[:8]
                retried_flash += 1
        self.session.commit()
        flash_ranked = sorted(
            [row for row in rows[:flash_limit] if row.flash_decision in {"PASS", "WATCH"} and row.midday_flash_score is not None],
            key=lambda row: (-float(row.midday_flash_score or 0), row.base_quant_rank, row.stock_code),
        )
        pro_limit = int(self.config.get("pool", {}).get("pro_pool_size", 20))
        retried_pro = 0
        missing_pro = [value for value in flash_ranked[:pro_limit] if value.midday_pro_score is None]
        pro_inputs = [(row.stock_code, {**_persisted_llm_payload(row), "flash": {
                    "stock_code": row.stock_code, "score": float(row.midday_flash_score),
                    "decision": row.flash_decision, "reasons": row.key_reasons[-3:], "risks": row.key_risks[-3:],
                }}) for row in missing_pro]
        pro_payloads, pro_failures = reviewer.review_many("PRO", pro_inputs, run_id) if pro_inputs else ({}, [])
        failures.extend(pro_failures)
        for row in missing_pro:
            payload = pro_payloads.get(row.stock_code)
            if payload is not None:
                row.midday_pro_score = _decimal(payload["score"])
                row.key_reasons = list(dict.fromkeys([*row.key_reasons, *payload.get("reasons", [])]))[:8]
                row.key_risks = list(dict.fromkeys([*row.key_risks, *payload.get("risks", [])]))[:8]
                retried_pro += 1
        usage = reviewer.usage()
        run.flash_count = sum(row.midday_flash_score is not None for row in rows[:flash_limit])
        run.pro_count = sum(row.midday_pro_score is not None for row in flash_ranked[:pro_limit])
        run.token_usage = int(run.token_usage or 0) + usage["tokens"]
        run.cost = _decimal(float(run.cost or 0) + usage["cost"])
        checkpoint = dict(run.checkpoint_json or {})
        checkpoint.update({"targeted_retry": {"flash": retried_flash, "pro": retried_pro, "failures": failures}, "orders_created": 0})
        run.checkpoint_json = checkpoint
        run.status = "WAITING_AFTERNOON_RECHECK" if not failures and run.flash_count == flash_limit else "PARTIAL_SUCCESS"
        run.current_stage = "WAITING_AFTERNOON_RECHECK" if run.status == "WAITING_AFTERNOON_RECHECK" else "COMPLETED"
        run.total_duration_ms = int(run.total_duration_ms or 0) + _duration_ms(started)
        run.completed_at = datetime.now(timezone.utc)
        self.session.commit()
        ranking = self.finalize_llm_ranking(run_id)
        return {**self.status(run_id), "targeted_retry": checkpoint["targeted_retry"], "final_count": ranking["final_count"]}

    def normalize_actions(self, run_id: str) -> dict[str, Any]:
        run = self.session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.run_id == run_id))
        if run is None:
            raise ValueError("MIDDAY_RUN_NOT_FOUND")
        rows = list(self.session.scalars(select(MiddayRecommendationResult).where(MiddayRecommendationResult.run_id == run_id)))
        positions: dict[str, list[TraderPositionSnapshot]] = {}
        for position in self.session.scalars(select(TraderPositionSnapshot).where(TraderPositionSnapshot.is_current.is_(True))):
            positions.setdefault(normalize_ts_code(position.stock_code), []).append(position)
        for row in rows:
            score = float(row.midday_enhanced_score)
            row.candidate_action = self.scoring._candidate_action(score, row.hard_gate_status)
            row.held_action = self.scoring._held_action(score, row.hard_gate_status, {"positions": positions.get(row.stock_code, [])}) if row.position_status == "HELD" else None
        candidate = distribution([row.candidate_action for row in rows])
        held = distribution([row.held_action for row in rows if row.held_action])
        hard_gate = distribution([row.hard_gate_status for row in rows])
        checkpoint = dict(run.checkpoint_json or {})
        checkpoint.update({"candidate_action_distribution": candidate, "held_action_distribution": held, "hard_gate_distribution": hard_gate, "orders_created": 0})
        run.checkpoint_json = checkpoint
        self.session.commit()
        return {"run_id": run_id, "candidate_action_distribution": candidate, "held_action_distribution": held, "hard_gate_distribution": hard_gate, "orders_created": 0}

    def reconcile_usage(self, run_id: str) -> dict[str, Any]:
        run = self.session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.run_id == run_id))
        if run is None:
            raise ValueError("MIDDAY_RUN_NOT_FOUND")
        rows = list(self.session.scalars(select(LLMUsage).where(
            LLMUsage.task.in_(["midday_flash_review_v1", "midday_pro_review_v1"]),
            LLMUsage.created_at >= run.created_at,
        )))
        tokens = sum(int(row.total_tokens or 0) for row in rows)
        cost = sum(float(row.cost_usd or 0) for row in rows)
        status_counts = distribution([str(row.status or "UNKNOWN") for row in rows])
        model_counts = distribution([str(row.model_name or "UNKNOWN") for row in rows])
        task_counts = distribution([str(row.task or "UNKNOWN") for row in rows])
        run.token_usage = tokens
        run.cost = _decimal(cost)
        checkpoint = dict(run.checkpoint_json or {})
        result_rows = list(self.session.scalars(select(MiddayRecommendationResult).where(MiddayRecommendationResult.run_id == run_id)))
        flash_ranked = sorted(
            [row for row in result_rows if row.midday_flash_score is not None and row.flash_decision in {"PASS", "WATCH"}],
            key=lambda row: (-float(row.midday_flash_score or 0), row.base_quant_rank, row.stock_code),
        )[:int(self.config.get("pool", {}).get("pro_pool_size", 20))]
        unresolved = [{"stock_code": row.stock_code, "stage": "PRO", "error_category": "SCHEMA_RETRY_EXHAUSTED"} for row in flash_ranked if row.midday_pro_score is None]
        if checkpoint.get("failures"):
            checkpoint["historical_attempt_failures"] = checkpoint["failures"]
        checkpoint["failure_count"] = len(unresolved)
        checkpoint["failures"] = unresolved
        checkpoint["llm"] = {
            "attempts": len(rows), "usable_flash": run.flash_count, "usable_pro": run.pro_count,
            "tokens": tokens, "cost": round(cost, 8),
        }
        checkpoint["llm_usage_reconciled"] = {
            "attempts": len(rows), "status_distribution": status_counts,
            "model_distribution": model_counts, "task_distribution": task_counts,
            "tokens": tokens, "cost": round(cost, 8),
        }
        run.checkpoint_json = checkpoint
        self.session.commit()
        return checkpoint["llm_usage_reconciled"]

    def refresh_persisted_scoring(self, run_id: str) -> dict[str, Any]:
        """Rebuild deterministic facts from persisted bars without external calls."""
        run = self.session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.run_id == run_id))
        if run is None:
            raise ValueError("MIDDAY_RUN_NOT_FOUND")
        rows = list(self.session.scalars(select(MiddayRecommendationResult).where(MiddayRecommendationResult.run_id == run_id)))
        if not rows:
            raise ValueError("MIDDAY_RESULTS_NOT_FOUND")
        minute_values = list(self.session.scalars(select(MarketMinuteBarShadow).where(
            MarketMinuteBarShadow.provider == "IFIND_HTTP",
            func.date(MarketMinuteBarShadow.bar_time) == run.session_trade_date.isoformat(),
        ).order_by(MarketMinuteBarShadow.stock_code, MarketMinuteBarShadow.bar_time)))
        minutes: dict[str, list[MarketMinuteBarShadow]] = {}
        for bar in minute_values:
            stamp = bar.bar_time.replace(tzinfo=SHANGHAI) if bar.bar_time.tzinfo is None else bar.bar_time.astimezone(SHANGHAI)
            if clock_time(10, 30) <= stamp.time().replace(tzinfo=None) <= clock_time(11, 30):
                minutes.setdefault(normalize_ts_code(bar.stock_code), []).append(bar)
        snapshot_values = list(self.session.scalars(select(MarketSnapshotShadow).where(
            MarketSnapshotShadow.provider == "IFIND_HTTP",
            func.date(MarketSnapshotShadow.snapshot_time) == run.session_trade_date.isoformat(),
        ).order_by(MarketSnapshotShadow.snapshot_time.desc())))
        snapshots: dict[str, MarketSnapshotShadow] = {}
        for quote in snapshot_values:
            stamp = quote.snapshot_time.replace(tzinfo=SHANGHAI) if quote.snapshot_time.tzinfo is None else quote.snapshot_time.astimezone(SHANGHAI)
            code = normalize_ts_code(quote.stock_code)
            if stamp.time().replace(tzinfo=None) <= clock_time(11, 30) and code not in snapshots:
                snapshots[code] = quote
        rescored: list[tuple[MiddayRecommendationResult, MiddayScore]] = []
        full_codes: list[str] = []
        for row in rows:
            bars = minutes.get(row.stock_code, [])
            complete_bars = bars if len(bars) >= 58 else []
            quote = _minute_snapshot(complete_bars, snapshots.get(row.stock_code)) if complete_bars else snapshots.get(row.stock_code)
            item = {
                "stock_code": row.stock_code, "sources": row.pool_sources,
                "position_status": row.position_status, "base_quant_rank": row.base_quant_rank,
                "base_quant_score": float(row.base_quant_score),
            }
            score = self.scoring.score(item, quote, complete_bars, index_return=None)
            if score.feature_scope == "MORNING_FULL_MINUTE":
                full_codes.append(row.stock_code)
            row.feature_scope = score.feature_scope
            row.midday_overlay_score = _decimal(score.overlay_score)
            row.midday_delta = _decimal(score.delta)
            row.midday_enhanced_score = _decimal(score.enhanced_score)
            row.midday_flash_score = None
            row.flash_decision = None
            row.midday_pro_score = None
            row.pro_rank = None
            row.hard_gate_status = score.hard_gate_status
            row.candidate_action = score.candidate_action
            row.held_action = score.held_action
            row.recommended_price = _decimal(score.prices["recommended_price"])
            row.max_acceptable_price = _decimal(score.prices["max_acceptable_price"])
            row.stop_loss = _decimal(score.prices["stop_loss"])
            row.take_profit_1 = _decimal(score.prices["take_profit_1"])
            row.take_profit_2 = _decimal(score.prices["take_profit_2"])
            row.suggested_weight = None
            row.suggested_position_percent = None
            row.key_reasons = score.reasons
            row.key_risks = score.risks
            row.data_quality = {**score.data_quality, "components": score.components, "cutoff": "11:30"}
            row.requires_manual_review = score.hard_gate_status != "PASS"
            row.actionable = False
            rescored.append((row, score))
        ranked = sorted(rescored, key=lambda value: (-value[1].enhanced_score, value[0].base_quant_rank, value[0].stock_code))
        for index, (row, score) in enumerate(ranked, 1):
            row.quick_snapshot_rank = index
            row.minute_rank = index if score.feature_scope == "MORNING_FULL_MINUTE" else None
        checkpoint = dict(run.checkpoint_json or {})
        checkpoint.update({
            "feature_scope_distribution": distribution([score.feature_scope for _, score in rescored]),
            "scoring_refreshed_from_persisted_1130_bars": True,
            "full_minute_codes": full_codes,
            "external_calls_during_refresh": 0,
            "orders_created": 0,
        })
        run.checkpoint_json = checkpoint
        run.status = "PARTIAL_SUCCESS"
        run.current_stage = "LLM_REVIEW_REQUIRED"
        run.flash_count = 0
        run.pro_count = 0
        run.final_count = 0
        run.token_usage = 0
        run.cost = _decimal(0)
        self.session.commit()
        return {"run_id": run_id, "status": run.status, "full_minute_count": len(full_codes), "scope_distribution": checkpoint["feature_scope_distribution"], "external_calls": 0, "orders_created": 0}

    def _persist_stopped(self, trade_date: date, now: datetime, gate: dict[str, Any], started: float) -> dict[str, Any]:
        try:
            baseline = MiddayBaselineResolver(self.session).resolve(trade_date, top_n=int(self.config.get("pool", {}).get("base_top_n", 100)))
        except ValueError:
            baseline = None
        input_hash = hashlib.sha256(json.dumps({
            "trade_date": trade_date.isoformat(), "status": gate["status"],
            "baseline": baseline["quant_run_id"] if baseline else None, "config": self.config,
        }, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":")).encode()).hexdigest()
        existing = self.session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.input_hash == input_hash))
        if existing:
            return _run_dict(existing)
        run = self._new_run(trade_date, now, gate, input_hash, baseline=baseline, pool=None)
        run.status = gate["status"]
        run.current_stage = "PREFLIGHT_STOPPED"
        run.completed_at = datetime.now(timezone.utc)
        run.total_duration_ms = _duration_ms(started)
        run.checkpoint_json = {"stop_reason": gate["status"], "external_calls": 0, "llm_calls": 0, "orders_created": 0}
        self.session.add(run)
        self.session.commit()
        return _run_dict(run)

    def _new_run(self, trade_date: date, now: datetime, gate: dict[str, Any], input_hash: str, *, baseline, pool) -> MiddayRecommendationRun:
        valid_from = datetime.combine(trade_date, clock_time.fromisoformat(str(self.config.get("valid_from", "13:00"))), tzinfo=SHANGHAI)
        valid_until = datetime.combine(trade_date, clock_time.fromisoformat(str(self.config.get("valid_until", "14:45"))), tzinfo=SHANGHAI)
        return MiddayRecommendationRun(
            run_id=f"midday-{uuid.uuid4().hex[:20]}", session_trade_date=trade_date,
            decision_time=now, market_session=gate["market_session"],
            baseline_trade_date=baseline["trade_date"] if baseline else None,
            baseline_quant_run_id=baseline["quant_run_id"] if baseline else None,
            baseline_manifest_id=baseline["manifest_id"] if baseline else None,
            status="RUNNING", current_stage="PREFLIGHT", target_session="AFTERNOON_SESSION",
            valid_from=valid_from, valid_until=valid_until,
            recheck_required=bool(self.config.get("require_afternoon_recheck", True)),
            source_mode=str(self.config.get("source_mode", "PREVIOUS_DAY_TUSHARE_WITH_MORNING_IFIND_SHADOW")),
            base_pool_count=len(pool["items"]) if pool else 0,
            input_hash=input_hash,
            config_snapshot={"midday_recommendation": self.config, "pool": pool and {key: value for key, value in pool.items() if key != "items"}},
            checkpoint_json={},
        )

    def _input_hash(self, trade_date: date, baseline: dict[str, Any], pool: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps({
            "trade_date": trade_date.isoformat(), "baseline": baseline["quant_run_id"],
            "manifest": baseline["manifest_id"], "pool_hash": pool["pool_hash"], "config": self.config,
        }, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":")).encode()).hexdigest()

    def _persist_results(self, run, scored, flash_results, pro_results, final_codes, snapshots) -> None:
        names = {normalize_ts_code(row.code): row.name for row in self.session.scalars(select(StockMaster).where(StockMaster.code.in_([item["stock_code"] for item, _ in scored])))}
        quick_rank = {item["stock_code"]: index for index, (item, _) in enumerate(sorted(scored, key=lambda value: (-value[1].enhanced_score, value[0]["base_quant_rank"])), 1)}
        pro_order = sorted(final_codes, key=lambda code: -float((pro_results.get(code) or {}).get("score", 0)))
        pro_rank = {code: index for index, code in enumerate(pro_order, 1)}
        weights = _equal_weights(final_codes)
        for item, score in scored:
            code = item["stock_code"]
            flash = flash_results.get(code)
            pro = pro_results.get(code)
            reasons = [*score.reasons, *((flash or {}).get("reasons") or []), *((pro or {}).get("reasons") or [])]
            risks = [*score.risks, *((flash or {}).get("risks") or []), *((pro or {}).get("risks") or [])]
            self.session.add(MiddayRecommendationResult(
                run_id=run.run_id, stock_code=code, stock_name=names.get(code) or _field(snapshots.get(code), "stock_name"),
                pool_sources=item["sources"], position_status=item["position_status"],
                base_quant_score=_decimal(item["base_quant_score"]), base_quant_rank=item["base_quant_rank"],
                feature_scope=score.feature_scope, midday_overlay_score=_decimal(score.overlay_score), midday_delta=_decimal(score.delta),
                midday_enhanced_score=_decimal(score.enhanced_score), quick_snapshot_rank=quick_rank[code],
                minute_rank=quick_rank[code] if score.feature_scope == "MORNING_FULL_MINUTE" else None,
                midday_flash_score=_decimal((flash or {}).get("score")), flash_decision=(flash or {}).get("decision"),
                midday_pro_score=_decimal((pro or {}).get("score")), pro_rank=pro_rank.get(code),
                hard_gate_status=score.hard_gate_status, candidate_action=score.candidate_action,
                held_action=score.held_action, recommended_price=_decimal(score.prices["recommended_price"]),
                max_acceptable_price=_decimal(score.prices["max_acceptable_price"]), stop_loss=_decimal(score.prices["stop_loss"]),
                take_profit_1=_decimal(score.prices["take_profit_1"]), take_profit_2=_decimal(score.prices["take_profit_2"]),
                suggested_weight=weights.get(code),
                suggested_position_percent=weights.get(code),
                valid_until=run.valid_until, recheck_required=run.recheck_required,
                key_reasons=reasons[:8], key_risks=risks[:8], data_quality={**score.data_quality, "components": score.components},
                requires_manual_review=score.hard_gate_status != "PASS" or (flash or {}).get("decision") == "MANUAL_REVIEW" or (pro or {}).get("decision") == "MANUAL_REVIEW",
                actionable=False,
            ))

    def _apply_review_results(
        self,
        run_id: str,
        flash_results: dict[str, dict[str, Any]],
        pro_results: dict[str, dict[str, Any]],
        final_codes: list[str],
    ) -> None:
        rows = list(self.session.scalars(select(MiddayRecommendationResult).where(
            MiddayRecommendationResult.run_id == run_id,
        )))
        pro_order = sorted(final_codes, key=lambda code: -float((pro_results.get(code) or {}).get("score", 0)))
        pro_rank = {code: index for index, code in enumerate(pro_order, 1)}
        weights = _equal_weights(final_codes)
        for row in rows:
            flash = flash_results.get(row.stock_code)
            pro = pro_results.get(row.stock_code)
            row.midday_flash_score = _decimal((flash or {}).get("score"))
            row.flash_decision = (flash or {}).get("decision")
            row.midday_pro_score = _decimal((pro or {}).get("score"))
            row.pro_rank = pro_rank.get(row.stock_code)
            row.suggested_weight = weights.get(row.stock_code)
            row.suggested_position_percent = weights.get(row.stock_code)
            row.key_reasons = list(dict.fromkeys([
                *list(row.key_reasons or []),
                *((flash or {}).get("reasons") or []),
                *((pro or {}).get("reasons") or []),
            ]))[:8]
            row.key_risks = list(dict.fromkeys([
                *list(row.key_risks or []),
                *((flash or {}).get("risks") or []),
                *((pro or {}).get("risks") or []),
            ]))[:8]
            row.requires_manual_review = (
                row.hard_gate_status != "PASS"
                or (flash or {}).get("decision") == "MANUAL_REVIEW"
                or (pro or {}).get("decision") == "MANUAL_REVIEW"
            )


def _run_dict(run: MiddayRecommendationRun | None) -> dict[str, Any]:
    if run is None:
        return {"status": "NOT_RUN"}
    return {
        "run_id": run.run_id, "trade_date": run.session_trade_date, "decision_time": run.decision_time,
        "market_session": run.market_session, "baseline_trade_date": run.baseline_trade_date,
        "baseline_quant_run_id": run.baseline_quant_run_id, "baseline_manifest_id": run.baseline_manifest_id,
        "status": run.status, "current_stage": run.current_stage, "target_session": run.target_session,
        "valid_from": run.valid_from, "valid_until": run.valid_until, "recheck_required": run.recheck_required,
        "source_mode": run.source_mode, "base_pool_count": run.base_pool_count,
        "snapshot_count": run.snapshot_count, "minute_count": run.minute_count,
        "flash_count": run.flash_count, "pro_count": run.pro_count, "final_count": run.final_count,
        "held_count": run.held_count, "token_usage": run.token_usage, "cost": float(run.cost or 0),
        "total_duration_ms": run.total_duration_ms, "checkpoint": run.checkpoint_json,
        "excel_path": run.excel_path, "completed_at": run.completed_at,
        "advisory_only": True, "actionable": False, "orders_created": 0,
    }


def _result_dict(row: MiddayRecommendationResult) -> dict[str, Any]:
    return {
        "stock_code": row.stock_code, "stock_name": row.stock_name, "pool_sources": row.pool_sources,
        "position_status": row.position_status, "base_quant_score": float(row.base_quant_score),
        "base_quant_rank": row.base_quant_rank, "feature_scope": row.feature_scope,
        "midday_overlay_score": float(row.midday_overlay_score) if row.midday_overlay_score is not None else None,
        "midday_delta": float(row.midday_delta), "midday_enhanced_score": float(row.midday_enhanced_score),
        "flash_score": float(row.midday_flash_score) if row.midday_flash_score is not None else None,
        "flash_decision": row.flash_decision, "pro_score": float(row.midday_pro_score) if row.midday_pro_score is not None else None,
        "pro_rank": row.pro_rank, "hard_gate_status": row.hard_gate_status,
        "candidate_action": row.candidate_action, "held_action": row.held_action,
        "recommended_price": _float(row.recommended_price), "max_acceptable_price": _float(row.max_acceptable_price),
        "stop_loss": _float(row.stop_loss), "take_profit_1": _float(row.take_profit_1), "take_profit_2": _float(row.take_profit_2),
        "suggested_weight": _float(row.suggested_weight), "valid_until": row.valid_until,
        "recheck_required": row.recheck_required, "key_reasons": row.key_reasons, "key_risks": row.key_risks,
        "data_quality": row.data_quality, "requires_manual_review": row.requires_manual_review, "actionable": False,
    }


def _llm_payload(item: dict[str, Any], score: MiddayScore) -> dict[str, Any]:
    return {
        "stock_code": item["stock_code"], "position_status": item["position_status"],
        "pool_sources": item["sources"], "base_quant_rank": item["base_quant_rank"],
        "base_quant_score": item["base_quant_score"], "feature_scope": score.feature_scope,
        "midday_overlay_score": score.overlay_score, "midday_delta": score.delta,
        "midday_enhanced_score": score.enhanced_score, "components": score.components,
        "hard_gate_status": score.hard_gate_status, "data_quality": score.data_quality,
    }


def _persisted_llm_payload(row: MiddayRecommendationResult) -> dict[str, Any]:
    return {
        "stock_code": row.stock_code, "position_status": row.position_status,
        "pool_sources": row.pool_sources, "base_quant_rank": row.base_quant_rank,
        "base_quant_score": float(row.base_quant_score), "feature_scope": row.feature_scope,
        "midday_overlay_score": _float(row.midday_overlay_score), "midday_delta": float(row.midday_delta),
        "midday_enhanced_score": float(row.midday_enhanced_score),
        "components": (row.data_quality or {}).get("components", {}),
        "hard_gate_status": row.hard_gate_status,
        "data_quality": {key: value for key, value in (row.data_quality or {}).items() if key != "components"},
    }


def _pool_priority(item: dict[str, Any]) -> int:
    priority = {"HUMAN_POSITION": 0, "AI_POSITION": 1, "ORDER_PLAN": 2, "MANUAL": 3, "BASE_TOP100": 4}
    return min(priority.get(source, 9) for source in item["sources"])


def _median_index_return(rows: list[Any]) -> float | None:
    values = []
    for row in rows:
        latest, previous = _number(_field(row, "latest")), _number(_field(row, "pre_close"))
        if latest and previous:
            values.append(latest / previous - 1.0)
    return median(values) if values else None


def _minute_snapshot(rows: list[Any], prior: MarketSnapshotShadow | None) -> dict[str, Any]:
    closes = [float(row.close) for row in rows if row.close is not None]
    highs = [float(row.high) for row in rows if row.high is not None]
    lows = [float(row.low) for row in rows if row.low is not None]
    opens = [float(row.open) for row in rows if row.open is not None]
    return {
        "latest": closes[-1] if closes else None,
        "open": opens[0] if opens else None,
        "high": max(highs) if highs else None,
        "low": min(lows) if lows else None,
        "pre_close": _field(prior, "pre_close"),
        "limit_up": _field(prior, "limit_up"),
        "limit_down": _field(prior, "limit_down"),
        "stock_name": _field(prior, "stock_name"),
    }


def _row_code(row: Any) -> str:
    return str(_field(row, "stock_code") or _field(row, "index_code") or "")


def _field(row: Any | None, name: str) -> Any:
    if row is None:
        return None
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(round(float(value), 8)))


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _duration_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _equal_weights(codes: list[str]) -> dict[str, Decimal]:
    if not codes:
        return {}
    quantum = Decimal("0.0000000001")
    base = (Decimal(1) / Decimal(len(codes))).quantize(quantum)
    values = {code: base for code in codes[:-1]}
    values[codes[-1]] = Decimal(1) - base * Decimal(len(codes) - 1)
    return values
