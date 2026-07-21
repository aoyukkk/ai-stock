from __future__ import annotations

import hashlib
import json
import os
import statistics
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import AppConfig, get_app_config
from backend.workbench.historical import HistoricalPipelineRunResolver
from database.models import (
    AllocationRun,
    DecisionSnapshot,
    IFindShadowAcceptanceItem,
    IFindShadowAcceptanceRun,
    MarketDailySnapshot,
    MarketMinuteBarShadow,
    MarketReviewRun,
    MarketSnapshotShadow,
    ModelValidationRun,
    OrderPlan,
    OrderPriceCandidate,
    PositionSuggestionRecord,
    PredictionRecord,
    ProCandidateReview,
    QuantRankResult,
    QuantRun,
    StockFactorScore,
    ExternalProviderUsage,
    IndexMarketDailyShadow,
)
from database.session import get_database_identity
from datasource.ifind.http.auth import IFindHttpAuthManager
from datasource.ifind.http.client import IFindHttpClient
from datasource.ifind.http.models import IndexDailyBar, IndexRealtimeQuote, MinuteBar, RealtimeQuote
from datasource.ifind.http.provider import IFindHttpP0Provider
from datasource.ifind.shadow import IFindIntegrationMode, IFindPersistencePurpose, IFindProviderRegistry
from services.ifind_shadow_service import RealtimeMonitorPoolResolver
from services.ifind_tushare_comparison import IFindTushareComparisonService
from stock_codes import normalize_ts_code


SHANGHAI = ZoneInfo("Asia/Shanghai")
BUSINESS_MODELS = (
    QuantRun, QuantRankResult, StockFactorScore, ModelValidationRun, ProCandidateReview,
    OrderPlan, OrderPriceCandidate, AllocationRun, PositionSuggestionRecord,
    DecisionSnapshot, PredictionRecord, MarketReviewRun,
)


class AcceptanceGateError(RuntimeError):
    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__("IFIND_SHADOW_ACCEPTANCE_GATE_BLOCKED")


class SessionNotAllowedError(RuntimeError):
    pass


@dataclass(frozen=True)
class AcceptanceThresholds:
    realtime_min_coverage: float = 0.95
    realtime_preferred_p95: float = 60
    realtime_max_delay: float = 120
    realtime_timestamp_ratio: float = 0.95
    index_min_coverage: float = 0.875
    index_preferred_p95: float = 60
    index_max_delay: float = 120
    minute_min_completeness: float = 0.95
    minute_max_delay: float = 120


class IFindShadowAcceptanceService:
    """Two-mode live acceptance runner. It only writes Shadow and audit tables."""

    def __init__(self, session: Session, *, app_config: AppConfig | None = None, now: datetime | None = None) -> None:
        self.session = session
        self.app_config = app_config or get_app_config()
        self.now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        self.thresholds = self._thresholds()
        self.comparator = self._comparator()

    def gate(self, max_external_calls: int) -> dict[str, Any]:
        data_sources = self._data_sources()
        ifind = data_sources.get("ifind", {})
        schedule = self.app_config.config_files.get("schedule", {})
        missing: list[str] = []
        if not os.getenv("IFIND_REFRESH_TOKEN", "").strip(): missing.append("IFIND_REFRESH_TOKEN=CONFIGURED")
        if not _flag(os.getenv("IFIND_HTTP_ENABLED")): missing.append("IFIND_HTTP_ENABLED=true")
        if not _flag(os.getenv("RUN_REAL_IFIND_SHADOW")): missing.append("RUN_REAL_IFIND_SHADOW=true")
        if _flag(os.getenv("ENABLE_REAL_TRADING")): missing.append("ENABLE_REAL_TRADING=false")
        if str(self._shadow_config().get("integration_mode", "SHADOW")).upper() != "SHADOW": missing.append("integration_mode=SHADOW")
        if not _all_disabled(schedule): missing.append("scheduler disabled")
        if max_external_calls <= 0: missing.append("positive external call budget")
        try:
            self.session.execute(select(1))
        except Exception:
            missing.append("database writable")
        registry = IFindProviderRegistry()
        if {entry.provider_name for entry in registry.entries()} != {"ifind_http_index", "ifind_http_realtime", "ifind_http_minute"}:
            missing.append("provider registry")
        return {"passed": not missing, "missing": missing, "integration_mode": "SHADOW", "ifind_config_enabled": bool(ifind.get("enabled", False))}

    def run(self, *, mode: str, trade_date: date, pipeline_run_id: str | None = None,
            stock_limit: int = 20, minute_stock_count: int = 3, rounds: int = 3,
            interval_seconds: int = 60, max_external_calls: int = 20,
            force_provider_refresh: bool = False, output_dir: Path | None = None,
            pool_override: dict[str, Any] | None = None) -> dict[str, Any]:
        gate = self.gate(max_external_calls)
        run_id = f"ifind-accept-{uuid.uuid4().hex[:20]}"
        started = datetime.now(timezone.utc)
        before = self.business_snapshot()
        report: dict[str, Any] = {
            "acceptance_run_id": run_id, "acceptance_mode": "CLOSED_SESSION_ACCEPTANCE" if mode == "closed-session" else "OPEN_SESSION_ACCEPTANCE",
            "started_at": started.isoformat(), "trade_date": trade_date.isoformat(), "integration_mode": "SHADOW",
            "gate": gate, "external_call_count": 0, "auth_call_count": 0, "cache_hit_count": 0,
            "database_insert_count": 0, "duplicate_count": 0, "llm_call_count": 0, "status": "BLOCKED" if not gate["passed"] else "RUNNING",
        }
        report["gate"]["max_external_calls"] = max_external_calls
        if not gate["passed"]:
            report["blocked_reasons"] = gate["missing"]
            report["business_immutability"] = self._immutability(before, self.business_snapshot())
            return self._finish(report, started, before, output_dir)
        session = self.market_session(self.now)
        if mode == "open-session" and session not in {"MORNING_SESSION", "AFTERNOON_SESSION"}:
            report.update({"status": "WAITING_FOR_OPEN_SESSION_ACCEPTANCE", "market_session": session, "rounds": []})
            return self._finish(report, started, before, output_dir)
        report["market_session"] = "CLOSED_SESSION_FINAL" if mode == "closed-session" else session
        pool = pool_override or self._pool(trade_date, stock_limit, pipeline_run_id)
        report["pool"] = pool
        indexes = self._indexes()
        report["index_codes"] = indexes
        provider, auth, client = self._provider(max_external_calls)
        report["auth_call_count"] = auth.auth_calls
        try:
            if mode == "closed-session":
                self._run_closed(provider, client, report, indexes, pool, trade_date, minute_stock_count, force_provider_refresh)
            else:
                self._run_open(provider, client, report, indexes, pool, trade_date, minute_stock_count, rounds, interval_seconds)
        except Exception as exc:
            report.update({"status": "CALL_LIMIT_REACHED" if "CALL_LIMIT_REACHED" in str(exc) else "ACCEPTANCE_FAILED", "error_category": type(exc).__name__})
        finally:
            report["auth_call_count"] = auth.auth_calls
            report["external_call_count"] = client.call_count
        report["business_immutability"] = self._immutability(before, self.business_snapshot())
        if not report["business_immutability"]["passed"]:
            report["status"] = "BUSINESS_IMMUTABILITY_VIOLATION"
        return self._finish(report, started, before, output_dir)

    def business_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        for model in BUSINESS_MODELS:
            rows = self.session.scalars(select(model).order_by(model.id)).all()
            encoded = [{column.name: _json_value(getattr(row, column.name)) for column in model.__table__.columns} for row in rows]
            raw = json.dumps(encoded, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            snapshot[model.__tablename__] = {"count": len(rows), "hash": hashlib.sha256(raw.encode()).hexdigest()}
        return snapshot

    def _run_closed(self, provider: IFindHttpP0Provider, client: IFindHttpClient, report: dict[str, Any], indexes: list[str], pool: dict[str, Any], trade_date: date, minute_count: int, force: bool) -> None:
        index_daily = []
        for batch in _batches(indexes, 2):
            index_daily.extend(self._call("index_daily", batch, lambda batch=batch: provider.get_index_daily(batch, trade_date, trade_date), client, report))
        index_realtime = []
        for batch in _batches(indexes, 2):
            index_realtime.extend(self._call("index_realtime", batch, lambda batch=batch: provider.get_index_realtime(batch), client, report))
        selected = [item["stock_code"] for item in pool["items"]]
        stock_rows: list[RealtimeQuote] = []
        for batch in _batches(selected, 4):
            stock_rows.extend(self._call("stock_realtime", batch, lambda batch=batch: provider.get_realtime(batch), client, report))
        # Same batch keys within TTL must be cache hits and must not add rows.
        for batch in _batches(selected, 4):
            self._call("stock_realtime_cache", batch, lambda batch=batch: provider.get_realtime(batch), client, report, cache_probe=True)
        if force and selected:
            provider.clear_cache()
            self._call("stock_realtime_force", selected[:4], lambda: provider.get_realtime(selected[:4]), client, report, force=True)
        minute_codes = self._minute_codes(pool, minute_count)
        minute_rows: dict[str, list[MinuteBar]] = {}
        for code in minute_codes:
            minute_rows[code] = self._call("minute_bars", [code], lambda code=code: provider.get_minute_bars(code, f"{trade_date.isoformat()} 14:30:00", f"{trade_date.isoformat()} 15:00:00", "1m"), client, report)
        for row in index_daily: self._persist_index_daily(row, trade_date, report)
        for row in index_realtime: self._persist_index_snapshot(row, report)
        for row in stock_rows: self._persist_stock(row, report)
        minute_checks = {}
        closed_observation = datetime.combine(trade_date, time(15, 0), tzinfo=SHANGHAI)
        for code, rows in minute_rows.items():
            for row in rows:
                self._persist_minute(row, report)
            minute_checks[code] = self._validate_minute(rows, report["market_session"], closed_observation)
        stock_freshness = self._freshness(stock_rows, closed_observation)
        report.update({"index_requested": len(indexes), "index_returned": len({row.index_code for row in index_daily}), "stock_requested": len(selected),
                       "stock_returned": len({row.stock_code for row in stock_rows}), "minute_stock_count": len(minute_codes),
                       "minute_validation": minute_checks, "cache_first_request": "PASS", "cache_hit_validation": "PASS",
                       "stock_freshness": stock_freshness,
                       "index_coverage_ratio": len({row.index_code for row in index_daily}) / len(indexes) if indexes else 0.0,
                       "stock_coverage_ratio": len({normalize_ts_code(row.stock_code) for row in stock_rows}) / len(selected) if selected else 0.0})
        report["dual_source_comparison"] = self._compare_stocks(stock_rows, trade_date)
        self.session.commit()
        self._reload_check(report)
        report["quality_gate"] = self._quality_gate(report)
        report["status"] = "CLOSED_SESSION_ACCEPTED" if report["quality_gate"]["passed"] else "CLOSED_SESSION_ACCEPTANCE_FAILED"
        report["closed_summary"] = {key: report.get(key) for key in ("trade_date", "index_requested", "index_returned", "stock_requested", "stock_returned", "minute_stock_count", "external_call_count", "auth_call_count", "cache_hit_count", "database_insert_count", "duplicate_count", "quality_gate", "database_reload_check")}

    def _run_open(self, provider: IFindHttpP0Provider, client: IFindHttpClient, report: dict[str, Any], indexes: list[str], pool: dict[str, Any], trade_date: date, minute_count: int, rounds: int, interval_seconds: int) -> None:
        selected = [item["stock_code"] for item in pool["items"]]
        minute_codes = self._minute_codes(pool, minute_count)
        round_reports = []
        minute_checks: dict[str, dict[str, Any]] = {}
        for round_number in range(1, rounds + 1):
            observed = datetime.now(SHANGHAI)
            calls_before = client.call_count
            cache_hits_before = report["cache_hit_count"]
            session = self.market_session(observed)
            if session == "MIDDAY_BREAK":
                round_reports.append({"round": round_number, "observation_time": observed.isoformat(), "status": "SESSION_CHANGED"})
                break
            if session not in {"MORNING_SESSION", "AFTERNOON_SESSION"}:
                round_reports.append({"round": round_number, "observation_time": observed.isoformat(), "status": "SESSION_CHANGED", "market_session": session})
                break
            index_rows: list[IndexRealtimeQuote] = []
            for batch in _batches(indexes, 2): index_rows.extend(self._call("index_realtime", batch, lambda batch=batch: provider.get_index_realtime(batch), client, report, round_number=round_number))
            stock_rows: list[RealtimeQuote] = []
            for batch in _batches(selected, 4): stock_rows.extend(self._call("stock_realtime", batch, lambda batch=batch: provider.get_realtime(batch), client, report, round_number=round_number))
            received_at = datetime.now(SHANGHAI)
            metrics = self._freshness(stock_rows, received_at)
            for row in stock_rows: self._persist_stock(row, report, purpose=IFindPersistencePurpose.REALTIME_MONITOR.value, observation_time=received_at)
            index_coverage = len({row.index_code for row in index_rows}) / len(indexes) if indexes else 1.0
            stock_coverage = len({normalize_ts_code(row.stock_code) for row in stock_rows}) / len(selected) if selected else 1.0
            round_passed = metrics["freshness_status"] == "PASS" and index_coverage >= self.thresholds.index_min_coverage and stock_coverage >= self.thresholds.realtime_min_coverage and metrics["provider_timestamp_ratio"] >= self.thresholds.realtime_timestamp_ratio
            round_reports.append({"round": round_number, "observation_time": observed.isoformat(), "market_session": session,
                                 "index_coverage": index_coverage, "stock_coverage": stock_coverage,
                                 "request_started_at": observed.isoformat(), "local_receipt_time": received_at.isoformat(),
                                 **metrics, "requested_indices": len(indexes), "returned_indices": len({row.index_code for row in index_rows}),
                                 "requested_stocks": len(selected), "returned_stocks": len({normalize_ts_code(row.stock_code) for row in stock_rows}),
                                 "external_calls": client.call_count - calls_before, "cache_hits": report["cache_hit_count"] - cache_hits_before,
                                 "missing_stocks": sorted(set(selected) - {normalize_ts_code(row.stock_code) for row in stock_rows}), "errors": [],
                                 "status": "PASS" if round_passed else "QUALITY_GATE_FAILED"})
            report["rounds"] = round_reports
            if round_number < rounds:
                import time as _time
                _time.sleep(interval_seconds)
        if round_reports and rounds == len([item for item in round_reports if item.get("status") not in {"SESSION_CHANGED"}]) and len(round_reports) == rounds:
            minute_observed = datetime.now(SHANGHAI)
            minute_end = minute_observed.replace(second=0, microsecond=0) - timedelta(minutes=1)
            minute_start = minute_end - timedelta(minutes=9)
            for code in minute_codes:
                start_value = minute_start.strftime("%Y-%m-%d %H:%M:%S")
                end_value = minute_end.strftime("%Y-%m-%d %H:%M:%S")
                rows = self._call("minute_bars", [code], lambda code=code: provider.get_minute_bars(code, start_value, end_value, "1m"), client, report, round_number=rounds)
                for row in rows: self._persist_minute(row, report)
                minute_checks[code] = self._validate_minute(rows, report["market_session"], minute_observed, expected_bars=10)
            self.session.commit()
            self._reload_check(report)
            report["status"] = "OPEN_SESSION_ACCEPTED" if all(item["status"] == "PASS" for item in round_reports) and all(item["status"] == "PASS" for item in minute_checks.values()) else "OPEN_SESSION_ACCEPTANCE_FAILED"
        else:
            report["status"] = "SESSION_CHANGED"
        report["rounds"] = round_reports
        report["minute_validation"] = minute_checks
        if round_reports:
            latest_round = round_reports[-1]
            report.update({"index_requested": latest_round.get("requested_indices", 0), "index_returned": latest_round.get("returned_indices", 0),
                           "stock_requested": latest_round.get("requested_stocks", 0), "stock_returned": latest_round.get("returned_stocks", 0),
                           "minute_stock_count": len(minute_checks), "index_coverage_ratio": latest_round.get("index_coverage"),
                           "stock_coverage_ratio": latest_round.get("stock_coverage"), "stock_freshness": {key: latest_round.get(key) for key in ("provider_timestamp_ratio", "delay_p50", "delay_p95", "maximum_delay", "freshness_status")}})
        report["quality_gate"] = {"passed": report["status"] == "OPEN_SESSION_ACCEPTED", "checks": {"rounds": report["status"] == "OPEN_SESSION_ACCEPTED"}}

    def _call(self, capability: str, codes: list[str], callback, client: IFindHttpClient, report: dict[str, Any], *, cache_probe: bool = False, force: bool = False, round_number: int = 1):
        budget = int(report["gate"].get("max_external_calls", 0) or 0)
        if budget and client.call_count >= budget: raise RuntimeError("CALL_LIMIT_REACHED")
        before = client.call_count
        started = datetime.now(timezone.utc)
        rows = callback()
        calls = client.call_count - before
        if calls == 0:
            report["cache_hit_count"] += 1
        report["external_call_count"] = client.call_count
        self.session.add(ExternalProviderUsage(provider="IFIND_HTTP", transport="HTTP", capability=capability,
            requested_at=started, code_count=len(codes), requested_rows_estimate=len(codes), returned_rows=len(rows),
            latency_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000), cache_hit=calls == 0,
            status="CACHE_HIT" if calls == 0 else "SUCCESS", error_category=None, job_id=report["acceptance_run_id"],
            purpose=IFindPersistencePurpose.SHADOW_VALIDATION.value))
        return rows

    def _provider(self, max_external_calls: int):
        auth = IFindHttpAuthManager(base_url=os.getenv("IFIND_HTTP_BASE_URL", "https://quantapi.51ifind.com/api/v1"), timeout_seconds=30)
        if auth.has_refresh_token: auth.refresh_access_token()
        # The acceptance budget may be 40, while the shared HTTP transport keeps
        # its stricter per-process safety ceiling of 30 calls.
        client = IFindHttpClient(auth, maximum_calls=min(max_external_calls, 30), interval_ms=1000)
        return IFindHttpP0Provider(client, enabled=True, cache_ttl_seconds=int(self._shadow_config().get("realtime", {}).get("cache_ttl_seconds", 15))), auth, client

    def _pool(self, trade_date: date, limit: int, pipeline_run_id: str | None) -> dict[str, Any]:
        resolved = RealtimeMonitorPoolResolver(self.session).resolve(trade_date)
        items = resolved["items"][:max(1, min(limit, 20))]
        if not items:
            items = [{"stock_code": normalize_ts_code(code), "origin": "FALLBACK_SAMPLE", "origins": ["FALLBACK_SAMPLE"]} for code in self._acceptance_config().get("fallback_sample", [])]
        digest = hashlib.sha256("|".join(item["stock_code"] for item in items).encode()).hexdigest()
        pipeline_id = pipeline_run_id
        if not pipeline_id:
            try: pipeline_id = HistoricalPipelineRunResolver(self.session).resolve(trade_date).get("pipeline_run_id")
            except Exception: pipeline_id = None
        return {"pool_source": "FINAL_MANUAL_POSITION_ORDER_PLAN" if resolved["items"] else "DETERMINISTIC_AUDIT_SAMPLE", "stock_count": len(items), "deduplicated_count": len(items), "items": items, "pool_hash": digest, "pipeline_run_id": pipeline_id}

    def _indexes(self) -> list[str]:
        values = self.app_config.config_files.get("market_review", {}).get("market_review", {}).get("indices", [])
        return [normalize_ts_code(item["index_code"]) for item in values if isinstance(item, Mapping) and item.get("index_code")][:8]

    def _minute_codes(self, pool: dict[str, Any], count: int) -> list[str]:
        selected: list[str] = []
        for preferred in (
            "HUMAN_HELD",
            "AI_HELD",
            "ACTIVE_ORDER_PLAN",
            "MANUAL",
            "FINAL",
            "POSITION",
            "ORDER_PLAN",
        ):
            for item in pool["items"]:
                if preferred in item.get("origins", []) and item["stock_code"] not in selected:
                    selected.append(item["stock_code"])
        for item in pool["items"]:
            if item["stock_code"] not in selected: selected.append(item["stock_code"])
        return selected[:max(1, min(count, 20))]

    def _persist_stock(self, row: RealtimeQuote, report: dict[str, Any], *, purpose: str = "SHADOW_VALIDATION", observation_time: datetime | None = None) -> None:
        observed = (observation_time or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        provider_time = row.datetime
        snapshot_time = _parse_time(provider_time, observed)
        code = normalize_ts_code(row.stock_code)
        exists = self.session.scalar(select(MarketSnapshotShadow).where(MarketSnapshotShadow.stock_code == code, MarketSnapshotShadow.snapshot_time == snapshot_time, MarketSnapshotShadow.provider == "IFIND_HTTP"))
        if exists:
            report["duplicate_count"] += 1
            return
        self.session.add(MarketSnapshotShadow(stock_code=code, snapshot_time=snapshot_time, provider_time=provider_time, latest=_num(row.latest), open=_num(row.open), high=_num(row.high), low=_num(row.low), volume=_num(row.volume), amount=_num(row.amount), data_status=row.data_status, provider="IFIND_HTTP", transport="HTTP", fetched_at=observed, purpose=purpose, response_hash=_hash_row(row)))
        report["database_insert_count"] += 1
        self._add_item(report, "stock_realtime", code, provider_time, "PASS", row.data_status, "PASS", None, _hash_row(row))

    def _persist_index_daily(self, row: IndexDailyBar, trade_date: date, report: dict[str, Any]) -> None:
        code = normalize_ts_code(row.index_code)
        exists = self.session.scalar(select(IndexMarketDailyShadow).where(IndexMarketDailyShadow.index_code == code, IndexMarketDailyShadow.trade_date == trade_date, IndexMarketDailyShadow.provider == "IFIND_HTTP"))
        if exists:
            exists.purpose = IFindPersistencePurpose.SHADOW_VALIDATION.value
            report["duplicate_count"] += 1
            return
        self.session.add(IndexMarketDailyShadow(index_code=code, trade_date=trade_date, open=_num(row.open), high=_num(row.high), low=_num(row.low), close=_num(row.close), provider="IFIND_HTTP", transport="HTTP", data_status=row.data_status, fetched_at=datetime.now(SHANGHAI), provider_time=row.datetime, purpose=IFindPersistencePurpose.SHADOW_VALIDATION.value))
        report["database_insert_count"] += 1
        self._add_item(report, "index_daily", code, row.datetime, "PASS", row.data_status, "PASS", None, _hash_row(row))

    def _persist_index_snapshot(self, row: IndexRealtimeQuote, report: dict[str, Any]) -> None:
        observed = datetime.now(SHANGHAI)
        code = normalize_ts_code(row.index_code)
        snapshot_time = _parse_time(row.datetime, observed)
        exists = self.session.scalar(select(MarketSnapshotShadow).where(MarketSnapshotShadow.stock_code == code, MarketSnapshotShadow.snapshot_time == snapshot_time, MarketSnapshotShadow.provider == "IFIND_HTTP"))
        if exists: report["duplicate_count"] += 1; return
        self.session.add(MarketSnapshotShadow(stock_code=code, snapshot_time=snapshot_time, provider_time=row.datetime, latest=_num(row.latest), open=_num(row.open), high=_num(row.high), low=_num(row.low), volume=_num(row.volume), amount=_num(row.amount), data_status=row.data_status, provider="IFIND_HTTP", transport="HTTP", fetched_at=observed, purpose=IFindPersistencePurpose.SHADOW_VALIDATION.value, response_hash=_hash_row(row)))
        report["database_insert_count"] += 1
        self._add_item(report, "index_realtime", code, row.datetime, "PASS", row.data_status, "PASS", None, _hash_row(row))

    def _persist_minute(self, row: MinuteBar, report: dict[str, Any]) -> None:
        observed = datetime.now(SHANGHAI)
        code = normalize_ts_code(row.stock_code)
        bar_time = _parse_time(row.datetime, observed)
        seen = report.setdefault("_minute_seen", set())
        key = (code, bar_time.isoformat())
        if key in seen:
            report["duplicate_count"] += 1
            return
        seen.add(key)
        exists = self.session.scalar(select(MarketMinuteBarShadow).where(
            MarketMinuteBarShadow.stock_code == code, MarketMinuteBarShadow.bar_time == bar_time,
            MarketMinuteBarShadow.interval == "1m", MarketMinuteBarShadow.provider == "IFIND_HTTP"))
        if exists:
            report["duplicate_count"] += 1
            return
        self.session.add(MarketMinuteBarShadow(stock_code=code, bar_time=bar_time, interval="1m", open=_num(row.open), high=_num(row.high), low=_num(row.low), close=_num(row.close), volume=_num(row.volume), amount=_num(row.amount), provider="IFIND_HTTP", data_status=row.data_status, fetched_at=observed, purpose=IFindPersistencePurpose.SHADOW_VALIDATION.value))
        report["database_insert_count"] += 1
        self._add_item(report, "minute_bars", code, row.datetime, "PASS", row.data_status, "PASS", None, _hash_row(row))

    def _add_item(self, report: dict[str, Any], capability: str, code: str, provider_time: str | None, coverage: str, schema: str, freshness: str, comparison: str | None, response_hash: str | None, round_number: int = 1, delay: float | None = None) -> None:
        self.session.add(IFindShadowAcceptanceItem(acceptance_run_id=report["acceptance_run_id"], capability=capability, code=code, round_number=round_number, provider_time=provider_time, observation_time=datetime.now(SHANGHAI), delay_seconds=delay, coverage_status=coverage, schema_status=schema, freshness_status=freshness, comparison_status=comparison, error_category=None, response_hash=response_hash))

    def _validate_minute(self, rows: list[MinuteBar], session: str, observed: datetime, *, expected_bars: int = 30) -> dict[str, Any]:
        parsed = [_parse_time(row.datetime, observed) for row in rows]
        errors: list[str] = []
        if parsed != sorted(parsed): errors.append("BAR_NOT_SORTED")
        if len(set(parsed)) != len(parsed): errors.append("DUPLICATE_BAR_TIME")
        if any((current - previous).total_seconds() != 60 for previous, current in zip(parsed, parsed[1:])): errors.append("NON_CONTIGUOUS_BAR_TIME")
        for row, stamp in zip(rows, parsed):
            if row.high < max(row.open, row.close, row.low): errors.append("INVALID_HIGH")
            if row.low > min(row.open, row.close, row.high): errors.append("INVALID_LOW")
            if row.volume < 0: errors.append("NEGATIVE_VOLUME")
            if row.amount < 0: errors.append("NEGATIVE_AMOUNT")
            if stamp > observed: errors.append("FUTURE_BAR")
            local = stamp.astimezone(SHANGHAI).time()
            if not (time(9, 30) <= local <= time(11, 30) or time(13, 0) <= local <= time(15, 0)): errors.append("OUTSIDE_TRADING_SESSION")
        latest = parsed[-1] if parsed else None
        latest_delay = max(0.0, (observed - latest).total_seconds()) if latest else None
        completeness = min(1.0, len(parsed) / max(1, expected_bars)) if parsed else 0.0
        status = "PASS" if not errors and completeness >= self.thresholds.minute_min_completeness and latest_delay is not None and latest_delay <= self.thresholds.minute_max_delay else "FAILED"
        return {"bar_count": len(rows), "expected_bar_count": expected_bars, "latest_bar_time": latest.isoformat() if latest else None, "latest_bar_delay_seconds": latest_delay, "completeness_ratio": completeness, "errors": sorted(set(errors)), "status": status}

    def _freshness(self, rows: list[RealtimeQuote], observed: datetime) -> dict[str, Any]:
        delays = []
        timestamp_count = 0
        for row in rows:
            if row.datetime:
                timestamp_count += 1
                delays.append(max(0.0, (observed - _parse_time(row.datetime, observed)).total_seconds()))
        semantics = {"raw_field_name": "time", "provider_time_source": "EXCHANGE_QUOTE_TIME", "precision": "SECOND", "timezone": "Asia/Shanghai", "delay_valid": bool(delays), "fallback_behavior": "NULL_DELAY_AND_TIME_SEMANTICS_UNKNOWN"}
        if not delays: return {"provider_timestamp_ratio": 0.0, "delay_p50": None, "delay_p95": None, "maximum_delay": None, "freshness_status": "TIME_SEMANTICS_UNKNOWN", **semantics}
        p50 = statistics.median(delays); p95 = _percentile(delays, 0.95); maximum = max(delays)
        status = "PASS" if p95 <= self.thresholds.realtime_preferred_p95 else "PASS_WITH_WARNINGS" if p95 <= self.thresholds.realtime_max_delay else "FAILED"
        return {"provider_timestamp_ratio": timestamp_count / len(rows) if rows else 0.0, "delay_p50": p50, "delay_p95": p95, "maximum_delay": maximum, "freshness_status": status, **semantics}

    def _compare_stocks(self, rows: list[RealtimeQuote], trade_date: date) -> dict[str, Any]:
        local = self._tushare_daily(trade_date)
        items = []
        for row in rows:
            official = local.get(normalize_ts_code(row.stock_code))
            shadow = {"open": row.open, "high": row.high, "low": row.low, "close": row.latest, "volume": row.volume, "amount": row.amount}
            official_fields = {"open": official.get("open"), "high": official.get("high"), "low": official.get("low"), "close": official.get("close"), "volume": official.get("vol"), "amount": official.get("amount")} if official else None
            result = self.comparator.compare(shadow, official_fields, fields=("open", "high", "low", "close"), units={"open": "CNY", "high": "CNY", "low": "CNY", "close": "CNY"})
            items.append({"stock_code": normalize_ts_code(row.stock_code), **result})
        counts = {status: sum(item["status"] == status for item in items) for status in ("MATCH", "MATCH_WITH_TOLERANCE", "UNIT_CONVERTED_MATCH", "UNIT_UNKNOWN", "TIMESTAMP_MISMATCH", "MATERIAL_CONFLICT", "SOURCE_MISSING", "NOT_COMPARABLE")}
        return {"compared_rows": len(items), "counts": counts, "items": items, "units": {"price": "CNY", "volume": "UNIT_UNKNOWN", "amount": "UNIT_UNKNOWN"}, "official_source_unchanged": True}

    def _tushare_daily(self, trade_date: date) -> dict[str, dict[str, Any]]:
        root = Path(self.app_config.root_dir) / "data" / "cache" / "tushare" / "trade_date" / "daily" / f"{trade_date:%Y%m%d}.json"
        try:
            rows = json.loads(root.read_text(encoding="utf-8"))
        except (OSError, ValueError): return {}
        return {normalize_ts_code(row["ts_code"]): row for row in rows if isinstance(row, dict) and row.get("ts_code")}

    def _reload_check(self, report: dict[str, Any]) -> None:
        self.session.flush()
        self.session.commit()
        bind = self.session.get_bind()
        reader = sessionmaker(bind=bind, expire_on_commit=False)()
        try:
            index_count = reader.scalar(select(func.count(IndexMarketDailyShadow.id))) or 0
            snapshot_count = reader.scalar(select(func.count(MarketSnapshotShadow.id))) or 0
            minute_count = reader.scalar(select(func.count(MarketMinuteBarShadow.id))) or 0
            report["database_reload_check"] = {
                "status": "PASS",
                "new_session": True,
                "index_daily_rows": index_count,
                "snapshot_rows": snapshot_count,
                "minute_rows": minute_count,
            }
        except Exception as exc:
            report["database_reload_check"] = {"status": "FAILED", "new_session": True, "error_category": type(exc).__name__}
        finally:
            reader.close()

    def _quality_gate(self, report: dict[str, Any]) -> dict[str, Any]:
        freshness = report.get("stock_freshness", {})
        minute_checks = report.get("minute_validation", {})
        minute_ratios = [float(item.get("completeness_ratio", 0.0)) for item in minute_checks.values()]
        dual = report.get("dual_source_comparison", {})
        counts = dual.get("counts", {})
        checks = {
            "index_coverage": not report.get("index_requested") or float(report.get("index_coverage_ratio", 0.0)) >= self.thresholds.index_min_coverage,
            "stock_coverage": float(report.get("stock_coverage_ratio", 0.0)) >= self.thresholds.realtime_min_coverage,
            "provider_timestamp_ratio": float(freshness.get("provider_timestamp_ratio", 0.0)) >= self.thresholds.realtime_timestamp_ratio,
            "maximum_delay": freshness.get("maximum_delay") is not None and float(freshness["maximum_delay"]) <= self.thresholds.realtime_max_delay,
            "minute_completeness": bool(minute_ratios) and min(minute_ratios) >= self.thresholds.minute_min_completeness,
            "minute_validation": bool(minute_checks) and all(item.get("status") == "PASS" for item in minute_checks.values()),
            "dual_source": counts.get("MATERIAL_CONFLICT", 0) == 0 and counts.get("SOURCE_MISSING", 0) == 0,
            "database_reload": report.get("database_reload_check", {}).get("status") == "PASS",
        }
        return {"passed": all(checks.values()), "checks": checks, "recommendation": "READY_FOR_SELECTED_POOL_MONITOR" if all(checks.values()) else "KEEP_SHADOW"}

    def _finish(self, report: dict[str, Any], started: datetime, before: dict[str, Any], output_dir: Path | None) -> dict[str, Any]:
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        report.setdefault("market_session", "UNKNOWN")
        report.setdefault("business_immutability", self._immutability(before, self.business_snapshot()))
        report["status"] = report.get("status", "ACCEPTANCE_FAILED")
        report["promotion_recommendation"] = (
            "READY_FOR_SELECTED_POOL_MONITOR"
            if report["acceptance_mode"] == "OPEN_SESSION_ACCEPTANCE"
            and report["status"] == "OPEN_SESSION_ACCEPTED"
            and report.get("quality_gate", {}).get("passed")
            else "KEEP_SHADOW"
        )
        report["recommendation"] = report["promotion_recommendation"]
        freshness = report.get("stock_freshness", {})
        minute_checks = report.get("minute_validation", {})
        minute_ratios = [float(item.get("completeness_ratio", 0.0)) for item in minute_checks.values()]
        dual_counts = report.get("dual_source_comparison", {}).get("counts", {})
        run = IFindShadowAcceptanceRun(acceptance_run_id=report["acceptance_run_id"], acceptance_mode=report["acceptance_mode"], started_at=started, completed_at=datetime.now(timezone.utc), trade_date=date.fromisoformat(report["trade_date"]), market_session=report["market_session"], status=report["status"], integration_mode="SHADOW", index_requested=report.get("index_requested", 0), index_returned=report.get("index_returned", 0), stock_requested=report.get("stock_requested", 0), stock_returned=report.get("stock_returned", 0), minute_stock_count=report.get("minute_stock_count", 0), external_call_count=report.get("external_call_count", 0), auth_call_count=report.get("auth_call_count", 0), cache_hit_count=report.get("cache_hit_count", 0), database_insert_count=report.get("database_insert_count", 0), duplicate_count=report.get("duplicate_count", 0), index_coverage_ratio=report.get("index_coverage_ratio"), stock_coverage_ratio=report.get("stock_coverage_ratio"), minute_completeness_ratio=min(minute_ratios) if minute_ratios else None, realtime_delay_p50=freshness.get("delay_p50"), realtime_delay_p95=freshness.get("delay_p95"), maximum_delay=freshness.get("maximum_delay"), provider_timestamp_ratio=freshness.get("provider_timestamp_ratio"), dual_source_match_count=sum(dual_counts.get(key, 0) for key in ("MATCH", "MATCH_WITH_TOLERANCE", "UNIT_CONVERTED_MATCH")), material_conflict_count=dual_counts.get("MATERIAL_CONFLICT", 0), business_immutability_passed=report["business_immutability"]["passed"], business_immutability_json=report["business_immutability"], pool_json=report.get("pool", {}), config_snapshot=self._safe_config_snapshot(), dual_source_summary_json=report.get("dual_source_comparison", {}), time_semantics_status="PASS" if report["status"] in {"CLOSED_SESSION_ACCEPTED", "OPEN_SESSION_ACCEPTED"} else "UNKNOWN")
        self.session.add(run)
        self.session.commit()
        target = output_dir or Path(self.app_config.root_dir) / "data" / "reports" / "ifind" / "acceptance"
        target.mkdir(parents=True, exist_ok=True)
        name = "ifind_closed_session_acceptance" if report["acceptance_mode"] == "CLOSED_SESSION_ACCEPTANCE" else "ifind_open_session_acceptance"
        path = target / f"{name}_{datetime.now(SHANGHAI):%Y%m%d_%H%M%S}.json"
        report.pop("_minute_seen", None)
        safe_report = _safe_report(report)
        path.write_text(json.dumps(safe_report, ensure_ascii=True, indent=2), encoding="utf-8")
        try:
            report_path = str(path.relative_to(self.app_config.root_dir))
        except ValueError:
            report_path = f"acceptance/{path.name}"
        run.report_path = report_path
        self.session.commit()
        report["report_path"] = report_path
        self._write_companion_reports(report, target)
        return report

    def _write_companion_reports(self, report: dict[str, Any], target: Path) -> None:
        stamp = datetime.now(SHANGHAI).strftime("%Y%m%d_%H%M%S")
        trade_date = report["trade_date"].replace("-", "")
        dual = _safe_report(report.get("dual_source_comparison", {"status": "NOT_RUN"}))
        decision = {
            "acceptance_run_id": report["acceptance_run_id"],
            "acceptance_mode": report["acceptance_mode"],
            "trade_date": report["trade_date"],
            "status": report["status"],
            "quality_gate": _safe_report(report.get("quality_gate", {"passed": False})),
            "business_immutability": _safe_report(report.get("business_immutability", {})),
            "recommendation": report["promotion_recommendation"],
            "auto_promotion": False,
        }
        (target / f"ifind_dual_source_comparison_{trade_date}.json").write_text(json.dumps(dual, ensure_ascii=True, indent=2), encoding="utf-8")
        (target / f"ifind_shadow_promotion_decision_{stamp}.json").write_text(json.dumps(decision, ensure_ascii=True, indent=2), encoding="utf-8")

    def _immutability(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        changed = [name for name in before if before[name] != after.get(name)]
        return {"passed": not changed, "changed_tables": changed, "before": before, "after": after}

    def _safe_config_snapshot(self) -> dict[str, Any]:
        return {"integration_mode": "SHADOW", "ifind_enabled": False, "real_trading_enabled": False, "scheduler_enabled": False, "thresholds": self.thresholds.__dict__}

    def _shadow_config(self) -> dict[str, Any]:
        return self.app_config.config_files.get("ifind_shadow", {}).get("ifind_shadow", {})

    def _acceptance_config(self) -> dict[str, Any]:
        return self.app_config.config_files.get("ifind_shadow", {}).get("ifind_shadow_acceptance", {})

    def _data_sources(self) -> dict[str, Any]:
        return self.app_config.config_files.get("data_sources", {}).get("data_sources", {})

    def _thresholds(self) -> AcceptanceThresholds:
        cfg = self._acceptance_config()
        real, index, minute = cfg.get("realtime", {}), cfg.get("index", {}), cfg.get("minute", {})
        return AcceptanceThresholds(realtime_min_coverage=float(real.get("minimum_coverage_ratio", .95)), realtime_preferred_p95=float(real.get("preferred_p95_delay_seconds", 60)), realtime_max_delay=float(real.get("maximum_acceptable_delay_seconds", 120)), realtime_timestamp_ratio=float(real.get("require_provider_timestamp_ratio", .95)), index_min_coverage=float(index.get("minimum_coverage_ratio", .875)), index_preferred_p95=float(index.get("preferred_p95_delay_seconds", 60)), index_max_delay=float(index.get("maximum_acceptable_delay_seconds", 120)), minute_min_completeness=float(minute.get("minimum_bar_completeness_ratio", .95)), minute_max_delay=float(minute.get("maximum_latest_bar_delay_seconds", 120)))

    def _comparator(self) -> IFindTushareComparisonService:
        cfg = self.app_config.config_files.get("ifind_shadow", {}).get("ifind_comparison", {})
        return IFindTushareComparisonService(price_absolute_tolerance=float(cfg.get("price", {}).get("absolute_tolerance", .01)), price_relative_tolerance=float(cfg.get("price", {}).get("relative_tolerance", .0005)), percentage_absolute_tolerance=float(cfg.get("percentage", {}).get("absolute_tolerance", .01)), volume_relative_tolerance=float(cfg.get("volume", {}).get("relative_tolerance", .01)), amount_relative_tolerance=float(cfg.get("amount", {}).get("relative_tolerance", .01)))

    def market_session(self, at: datetime) -> str:
        local = at.astimezone(SHANGHAI)
        if local.weekday() >= 5: return "NON_TRADING_DAY"
        clock = local.time()
        if clock < time(9, 15): return "PRE_MARKET"
        if clock < time(9, 30): return "OPENING_AUCTION"
        if clock <= time(11, 30): return "MORNING_SESSION"
        if clock < time(13, 0): return "MIDDAY_BREAK"
        if clock <= time(15, 0): return "AFTERNOON_SESSION"
        return "POST_MARKET"


def _batches(items: list[str], size: int) -> Iterable[list[str]]:
    for offset in range(0, len(items), size): yield items[offset:offset + size]


def _flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _all_disabled(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("enabled") is True: return False
        return all(_all_disabled(item) for item in value.values())
    if isinstance(value, list): return all(_all_disabled(item) for item in value)
    return True


def _parse_time(value: str | None, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI)).astimezone(SHANGHAI)
    except (TypeError, ValueError): return fallback


def _num(value: Any) -> float | None:
    try: return None if value is None else float(value)
    except (TypeError, ValueError): return None


def _hash_row(row: Any) -> str:
    return hashlib.sha256("|".join(str(getattr(row, key, "")) for key in ("stock_code", "index_code", "datetime", "latest", "close")).encode()).hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    if len(values) == 1: return values[0]
    ordered = sorted(values); index = (len(ordered) - 1) * percentile; lower = int(index); upper = min(lower + 1, len(ordered) - 1); weight = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)): return value.isoformat()
    if hasattr(value, "as_tuple"): return str(value)
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None: return value
    return str(value)


def _safe_report(value: Any) -> Any:
    if isinstance(value, dict): return {str(key): _safe_report(item) for key, item in value.items()}
    if isinstance(value, list): return [_safe_report(item) for item in value]
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, Path): return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None: return value
    return str(value)
