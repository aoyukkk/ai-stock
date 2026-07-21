from __future__ import annotations

import hashlib
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import Any, Iterable

from sqlalchemy import func, select

from database.models.ifind_shadow import (
    ExternalProviderUsage,
    IndexMarketDailyShadow,
    MarketMinuteBarShadow,
    MarketSnapshotShadow,
)
from database.models.order_plan import OrderPlan
from database.models.trading import Position
from database.models.validation import ProCandidateReview
from database.models.workbench import ManualSelectionRecord
from datasource.ifind.http.auth import IFindHttpAuthManager
from datasource.ifind.http.client import IFindHttpClient
from datasource.ifind.http.models import IndexDailyBar, IndexRealtimeQuote, MinuteBar, RealtimeQuote
from datasource.ifind.http.provider import IFindHttpP0Provider
from datasource.ifind.shadow import (
    IFindIntegrationMode,
    IFindPersistencePurpose,
    IFindProviderRegistry,
    assert_persistence_purpose,
)
from stock_codes import normalize_ts_code


class IFindShadowService:
    """Read-only Shadow orchestration. Official business tables are never written."""

    def __init__(self, session, *, provider: Any | None = None, config: Any | None = None) -> None:
        self.session = session
        self.provider = provider
        self.config = config
        self.registry = IFindProviderRegistry(enabled=self.runtime_enabled, mode=self.integration_mode)

    @property
    def data_sources(self) -> dict[str, Any]:
        return (self.config.config_files.get("data_sources", {}).get("data_sources", {}) if self.config else {})

    @property
    def shadow_config(self) -> dict[str, Any]:
        return (self.config.config_files.get("ifind_shadow", {}).get("ifind_shadow", {}) if self.config else {})

    @property
    def integration_mode(self) -> IFindIntegrationMode:
        value = self.shadow_config.get("integration_mode") or self.data_sources.get("ifind", {}).get("integration_mode", "SHADOW")
        try:
            return IFindIntegrationMode(str(value).upper())
        except ValueError:
            return IFindIntegrationMode.SHADOW

    @property
    def runtime_enabled(self) -> bool:
        configured = self.data_sources.get("ifind", {})
        return bool(
            self.integration_mode != IFindIntegrationMode.DISABLED
            and configured.get("enabled", False)
            and self.shadow_config.get("enabled", False)
            and os.getenv("IFIND_HTTP_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        )

    def provider_status(self) -> dict[str, Any]:
        usage = self.session.execute(
            select(func.count(ExternalProviderUsage.id), func.max(ExternalProviderUsage.requested_at)).where(
                ExternalProviderUsage.provider == "IFIND_HTTP"
            )
        ).one()
        return {
            "provider": "IFIND_HTTP",
            "transport": "HTTP",
            "integration_mode": self.integration_mode.value,
            "enabled": self.runtime_enabled,
            "configured": bool(os.getenv("IFIND_ACCESS_TOKEN") or os.getenv("IFIND_REFRESH_TOKEN")),
            "status": "READY" if self.runtime_enabled else "DISABLED",
            "observed_session_status": "CLOSED_SESSION_FINAL",
            "fallback_provider": "tushare",
            "scheduler_enabled": False,
            "real_trading_enabled": False,
            "usage_count": int(usage[0] or 0),
            "last_call_at": usage[1].isoformat() if usage[1] else None,
            "quota_remaining": None,
            "registry": self.registry.as_dict(),
        }

    def ensure_provider(self) -> Any | None:
        if self.provider is not None:
            return self.provider
        if not self.runtime_enabled:
            return None
        auth = IFindHttpAuthManager(
            base_url=os.getenv("IFIND_HTTP_BASE_URL", "https://quantapi.51ifind.com/api/v1"),
            timeout_seconds=float(self.shadow_config.get("timeout_seconds", 30)),
        )
        client = IFindHttpClient(auth, maximum_calls=8, interval_ms=1000)
        self.provider = IFindHttpP0Provider(client, enabled=True, cache_ttl_seconds=15)
        return self.provider

    def refresh_stocks(self, stock_codes: list[str], *, job_id: str | None = None, force: bool = False) -> dict[str, Any]:
        codes = _unique_codes(stock_codes)[:100]
        result = {"job_id": job_id or str(uuid.uuid4()), "pool_count": len(codes), "batch_count": _batch_count(len(codes), 4),
                  "cache_status": "MISS" if force else "DEFAULT", "status": "OBSERVATION_ONLY", "items": []}
        provider = self.ensure_provider()
        if provider is None:
            result.update({"status": "PROVIDER_DISABLED", "cache_status": "NOT_REQUESTED"})
            return result
        for batch in _batches(codes, 4):
            started = datetime.now(timezone.utc)
            try:
                rows = provider.get_realtime(batch)
                items = [self._persist_snapshot(row, result["job_id"]) for row in rows]
                result["items"].extend(items)
                self._audit("stock_realtime", len(batch), len(rows), started, result["job_id"], "SUCCESS")
            except Exception as exc:
                self._audit("stock_realtime", len(batch), 0, started, result["job_id"], "FAILED", type(exc).__name__)
        self.session.commit()
        return result

    def refresh_indices(self, trade_date: date, *, job_id: str | None = None) -> dict[str, Any]:
        codes = ["000001.SH", "399001.SZ"]
        result = self.index_shadow(codes, trade_date)
        result["job_id"] = job_id or str(uuid.uuid4())
        result["batch_count"] = 1
        result["cache_status"] = "NOT_REQUESTED" if result["comparison_status"] == "NOT_RUN" else "MISS"
        provider = self.ensure_provider()
        if provider is not None and hasattr(provider, "get_index_realtime"):
            started = datetime.now(timezone.utc)
            try:
                snapshot_rows = provider.get_index_realtime(codes)
                for row in snapshot_rows:
                    self._persist_index_realtime(row)
                self._audit("index_realtime", len(codes), len(snapshot_rows), started, result["job_id"], "SUCCESS")
                self.session.commit()
                result["realtime_count"] = len(snapshot_rows)
            except Exception as exc:
                self._audit("index_realtime", len(codes), 0, started, result["job_id"], "FAILED", type(exc).__name__)
                self.session.commit()
                result["realtime_count"] = 0
        else:
            result["realtime_count"] = 0
        return result

    def index_shadow(self, index_codes: list[str], trade_date: date) -> dict[str, Any]:
        codes = _unique_codes(index_codes)[:8]
        provider = self.ensure_provider()
        if provider is None:
            return {"trade_date": trade_date.isoformat(), "index_count": len(codes), "available_count": 0,
                    "missing_count": len(codes), "source_provider": "IFIND_HTTP", "comparison_status": "NOT_RUN",
                    "would_change_market_direction": False, "would_change_regime": False, "notes": ["provider disabled"]}
        rows = provider.get_index_latest_completed(codes, trade_date)
        for row in rows:
            self._persist_index(row, trade_date)
        self.session.commit()
        available = len(rows)
        return {"trade_date": trade_date.isoformat(), "index_count": len(codes), "available_count": available,
                "missing_count": max(0, len(codes) - available), "source_provider": "IFIND_HTTP",
                "comparison_status": "SHADOW_ONLY", "would_change_market_direction": False,
                "would_change_regime": False, "notes": ["official Market Review is unchanged"]}

    def list_results(self, *, page: int = 1, page_size: int = 50, keyword: str = "") -> dict[str, Any]:
        query = select(MarketSnapshotShadow).order_by(MarketSnapshotShadow.snapshot_time.desc())
        if keyword:
            query = query.where(MarketSnapshotShadow.stock_code.contains(keyword))
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
        return {"items": [_snapshot_dict(row) for row in rows], "total": int(total), "page": page, "page_size": page_size,
                "total_pages": (int(total) + page_size - 1) // page_size if total else 0}

    def list_indices(self, trade_date: date | None = None) -> list[dict[str, Any]]:
        query = select(IndexMarketDailyShadow).order_by(IndexMarketDailyShadow.trade_date.desc())
        if trade_date:
            query = query.where(IndexMarketDailyShadow.trade_date == trade_date)
        return [_index_dict(row) for row in self.session.scalars(query.limit(100)).all()]

    def minute_bars(self, stock_code: str, *, trade_date: date | None = None) -> list[dict[str, Any]]:
        code = normalize_ts_code(stock_code)
        query = select(MarketMinuteBarShadow).where(MarketMinuteBarShadow.stock_code == code).order_by(MarketMinuteBarShadow.bar_time)
        if trade_date:
            query = query.where(func.date(MarketMinuteBarShadow.bar_time) == trade_date.isoformat())
        return [_minute_dict(row) for row in self.session.scalars(query.limit(500)).all()]

    def load_minute(self, stock_code: str, start_time: str, end_time: str, *, job_id: str | None = None) -> dict[str, Any]:
        code = normalize_ts_code(stock_code)
        existing = self.minute_bars(code)
        if existing:
            return {"status": "CACHE_HIT", "stock_code": code, "items": existing, "bar_count": len(existing)}
        provider = self.ensure_provider()
        if provider is None:
            return {"status": "PROVIDER_DISABLED", "stock_code": code, "items": [], "bar_count": 0}
        started = datetime.now(timezone.utc)
        try:
            rows = provider.get_minute_bars(code, start_time, end_time, "1m")
            for row in rows:
                self.session.add(MarketMinuteBarShadow(stock_code=code, bar_time=_parse_time(row.datetime, datetime.now(timezone.utc)),
                    interval="1m", open=_finite(row.open), high=_finite(row.high), low=_finite(row.low), close=_finite(row.close),
                    volume=_finite(row.volume), amount=_finite(row.amount), provider="IFIND_HTTP", data_status=row.data_status,
                    fetched_at=datetime.now(timezone.utc), purpose=IFindPersistencePurpose.REALTIME_MONITOR.value))
            self._audit("minute_bars", 1, len(rows), started, job_id or str(uuid.uuid4()), "SUCCESS")
            self.session.commit()
            return {"status": "SUCCESS", "stock_code": code, "items": self.minute_bars(code), "bar_count": len(rows)}
        except Exception as exc:
            self._audit("minute_bars", 1, 0, started, job_id or str(uuid.uuid4()), "FAILED", type(exc).__name__)
            self.session.commit()
            return {"status": "FAILED", "stock_code": code, "items": [], "bar_count": 0, "error_category": type(exc).__name__}

    def tail_shadow(self, stock_code: str) -> dict[str, Any]:
        rows = self.minute_bars(stock_code)[-30:]
        closes = [row["close"] for row in rows if row["close"] is not None]
        volumes = [row["volume"] or 0 for row in rows]
        return {"stock_code": normalize_ts_code(stock_code), "status": "SHADOW_ONLY", "bar_count": len(rows),
                "last_price": closes[-1] if closes else None, "vwap": _vwap(rows),
                "return_30m": ((closes[-1] / closes[0]) - 1) * 100 if len(closes) > 1 and closes[0] else None,
                "volume_share": sum(volumes[-5:]) / sum(volumes) if sum(volumes) else None,
                "tail_structure": "NO_DATA" if not rows else ("ABOVE_VWAP" if closes[-1] >= (_vwap(rows) or closes[-1]) else "BELOW_VWAP")}

    def usage(self) -> dict[str, Any]:
        rows = self.session.scalars(select(ExternalProviderUsage).order_by(ExternalProviderUsage.requested_at.desc()).limit(20)).all()
        return {"items": [{"provider": row.provider, "capability": row.capability, "requested_at": row.requested_at.isoformat(),
                            "code_count": row.code_count, "returned_rows": row.returned_rows, "latency_ms": row.latency_ms,
                            "cache_hit": row.cache_hit, "status": row.status, "error_category": row.error_category,
                            "purpose": row.purpose} for row in rows], "quota_remaining": None}

    def _persist_snapshot(self, row: RealtimeQuote, job_id: str) -> dict[str, Any]:
        fetched = datetime.now(timezone.utc)
        snapshot_time = _parse_time(row.datetime, fetched)
        existing = self.session.scalar(select(MarketSnapshotShadow).where(
            MarketSnapshotShadow.stock_code == normalize_ts_code(row.stock_code),
            MarketSnapshotShadow.snapshot_time == snapshot_time,
            MarketSnapshotShadow.provider == "IFIND_HTTP",
        ))
        if existing:
            return _snapshot_dict(existing)
        item = MarketSnapshotShadow(stock_code=normalize_ts_code(row.stock_code), snapshot_time=snapshot_time,
            provider="IFIND_HTTP", transport="HTTP", latest=_finite(row.latest), open=_finite(row.open), high=_finite(row.high),
            low=_finite(row.low), volume=_finite(row.volume), amount=_finite(row.amount), data_status=row.data_status,
            fetched_at=fetched, provider_time=row.datetime, purpose=IFindPersistencePurpose.REALTIME_MONITOR.value,
            response_hash=hashlib.sha256(f"{row.stock_code}|{row.datetime}|{row.latest}".encode()).hexdigest())
        self.session.add(item)
        return _snapshot_dict(item)

    def _persist_index(self, row: IndexDailyBar, trade_date: date) -> None:
        code = normalize_ts_code(row.index_code)
        existing = self.session.scalar(select(IndexMarketDailyShadow).where(
            IndexMarketDailyShadow.index_code == code, IndexMarketDailyShadow.trade_date == trade_date,
            IndexMarketDailyShadow.provider == "IFIND_HTTP"))
        if existing:
            return
        self.session.add(IndexMarketDailyShadow(index_code=code, trade_date=trade_date,
            open=_finite(row.open), high=_finite(row.high), low=_finite(row.low), close=_finite(row.close), volume=_finite(row.volume),
            amount=_finite(row.amount), provider="IFIND_HTTP", transport="HTTP", data_status=row.data_status,
            fetched_at=datetime.now(timezone.utc), provider_time=row.datetime, purpose=IFindPersistencePurpose.MARKET_REVIEW_INDEX.value))

    def _persist_index_realtime(self, row: IndexRealtimeQuote) -> None:
        fetched = datetime.now(timezone.utc)
        code = normalize_ts_code(row.index_code)
        snapshot_time = _parse_time(row.datetime, fetched)
        existing = self.session.scalar(select(MarketSnapshotShadow).where(
            MarketSnapshotShadow.stock_code == code, MarketSnapshotShadow.snapshot_time == snapshot_time,
            MarketSnapshotShadow.provider == "IFIND_HTTP"))
        if existing:
            return
        self.session.add(MarketSnapshotShadow(stock_code=code, snapshot_time=snapshot_time, provider="IFIND_HTTP", transport="HTTP",
            latest=_finite(row.latest), open=_finite(row.open), high=_finite(row.high), low=_finite(row.low), volume=_finite(row.volume),
            amount=_finite(row.amount), data_status=row.data_status, fetched_at=fetched, provider_time=row.datetime,
            purpose=IFindPersistencePurpose.MARKET_REVIEW_INDEX.value,
            response_hash=hashlib.sha256(f"{code}|{row.datetime}|{row.latest}".encode()).hexdigest()))

    def _audit(self, capability: str, code_count: int, returned: int, started: datetime, job_id: str, status: str, error: str | None = None) -> None:
        self.session.add(ExternalProviderUsage(provider="IFIND_HTTP", transport="HTTP", capability=capability,
            requested_at=started, code_count=code_count, returned_rows=returned, latency_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            status=status, error_category=error, job_id=job_id, purpose=IFindPersistencePurpose.REALTIME_MONITOR.value))


class RealtimeMonitorPoolResolver:
    def __init__(self, session) -> None:
        self.session = session

    def resolve(self, trade_date: date) -> dict[str, Any]:
        sources: dict[str, set[str]] = {"POSITION": set(), "ORDER_PLAN": set(), "MANUAL": set(), "FINAL": set()}
        sources["POSITION"] = {normalize_ts_code(row.stock_code) for row in self.session.scalars(select(Position).where(Position.quantity > 0)).all()}
        sources["ORDER_PLAN"] = {normalize_ts_code(row.stock_code) for row in self.session.scalars(select(OrderPlan).where(OrderPlan.plan_date == trade_date)).all()}
        sources["MANUAL"] = {normalize_ts_code(row.stock_code) for row in self.session.scalars(select(ManualSelectionRecord).where(ManualSelectionRecord.trade_date == trade_date)).all()}
        for row in self.session.scalars(select(ProCandidateReview).order_by(ProCandidateReview.pro_rank.nulls_last(), ProCandidateReview.pro_score.desc()).limit(100)).all():
            sources["FINAL"].add(normalize_ts_code(row.stock_code))
        priority = {"POSITION": 0, "ORDER_PLAN": 1, "MANUAL": 2, "FINAL": 3}
        combined: dict[str, set[str]] = {}
        for origin, codes in sources.items():
            for code in codes:
                combined.setdefault(code, set()).add(origin)
        items = []
        for code, origins in sorted(combined.items(), key=lambda item: (min(priority[o] for o in item[1]), item[0]))[:100]:
            items.append({"stock_code": code, "origins": sorted(origins), "origin": "MULTIPLE" if len(origins) > 1 else next(iter(origins))})
        digest = hashlib.sha256("|".join(row["stock_code"] for row in items).encode()).hexdigest()
        return {"trade_date": trade_date.isoformat(), "items": items, "count": len(items), "pool_hash": digest}


def _batches(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _batch_count(count: int, size: int) -> int:
    return (count + size - 1) // size if count else 0


def _unique_codes(codes: list[str]) -> list[str]:
    result: list[str] = []
    for code in codes:
        normalized = normalize_ts_code(code)
        if normalized not in result:
            result.append(normalized)
    return result


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if isfinite(number) else None


def _parse_time(value: str, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return fallback


def _snapshot_dict(row: MarketSnapshotShadow) -> dict[str, Any]:
    return {key: getattr(row, key, None) for key in ("stock_code", "stock_name", "snapshot_time", "provider_time", "latest", "open", "high", "low", "pre_close", "change_percent", "volume", "amount", "data_status", "provider", "transport", "purpose")}


def _index_dict(row: IndexMarketDailyShadow) -> dict[str, Any]:
    return {key: getattr(row, key, None) for key in ("index_code", "index_name", "trade_date", "open", "high", "low", "close", "volume", "amount", "provider", "data_status", "purpose")}


def _minute_dict(row: MarketMinuteBarShadow) -> dict[str, Any]:
    return {key: getattr(row, key, None) for key in ("stock_code", "bar_time", "interval", "open", "high", "low", "close", "volume", "amount", "change_percent", "provider", "data_status", "purpose")}


def _vwap(rows: list[dict[str, Any]]) -> float | None:
    total_volume = sum(row.get("volume") or 0 for row in rows)
    return sum((row.get("close") or 0) * (row.get("volume") or 0) for row in rows) / total_volume if total_volume else None
