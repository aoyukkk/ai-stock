from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, time as clock_time, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend.core.config import AppConfig
from database.models import MarketMinuteBarShadow, MarketSnapshotShadow
from datasource.ifind.http.auth import IFindHttpAuthManager
from datasource.ifind.http.client import IFindHttpClient
from datasource.ifind.http.errors import IFindHttpError, IFindHttpErrorCategory
from datasource.ifind.http.provider import IFindHttpP0Provider
from datasource.ifind.http.rate_limiter import IFindHttpCallLimitReached
from stock_codes import normalize_ts_code


SHANGHAI = ZoneInfo("Asia/Shanghai")


class MiddayIFindCollector:
    def __init__(self, session, app_config: AppConfig, config: dict[str, Any], *, provider=None, auth=None, client=None, authorized_call_limit: int | None = None) -> None:
        self.session = session
        self.app_config = app_config
        self.config = config
        self.auth = auth
        self.client = client
        self.provider = provider
        self.authorized_call_limit = int(
            authorized_call_limit
            if authorized_call_limit is not None
            else config.get("max_external_calls", 75)
        )
        self.stats = {"auth": 0, "index": 0, "snapshot": 0, "minute": 0, "minute_failures": 0, "retries": 0, "cache_hits": 0}
        self.failures: list[dict[str, str]] = []

    def gate(self) -> dict[str, Any]:
        missing = []
        if not _flag(os.getenv("IFIND_HTTP_ENABLED")):
            missing.append("IFIND_HTTP_ENABLED=true")
        if not _flag(os.getenv("RUN_REAL_IFIND_SHADOW")):
            missing.append("RUN_REAL_IFIND_SHADOW=true")
        if _flag(os.getenv("ENABLE_REAL_TRADING")):
            missing.append("ENABLE_REAL_TRADING=false")
        if not (os.getenv("IFIND_ACCESS_TOKEN") or os.getenv("IFIND_REFRESH_TOKEN")):
            missing.append("IFIND_TOKEN_CONFIGURED")
        return {"passed": not missing, "missing": missing, "integration_mode": "SHADOW"}

    def collect_snapshots(self, trade_date: date, stock_codes: list[str]) -> dict[str, Any]:
        self._ensure_provider()
        index_codes = self._index_codes()
        index_rows = []
        for batch in _batches(index_codes, int(self.config.get("index_batch_size", 2))):
            index_rows.extend(self.provider.get_index_realtime(batch))
            self.stats["index"] += 1
        stock_rows = []
        for batch in _batches(stock_codes[:int(self.config.get("snapshot_max_stocks", 120))], int(self.config.get("snapshot_batch_size", 4))):
            stock_rows.extend(self.provider.get_realtime(batch))
            self.stats["snapshot"] += 1
        for row in index_rows:
            self._persist_snapshot(row.index_code, row, purpose="MIDDAY_INDEX")
        for row in stock_rows:
            self._persist_snapshot(row.stock_code, row, purpose="MIDDAY_SNAPSHOT")
        self.session.commit()
        self._sync_stats()
        return {
            "index_rows": index_rows,
            "stock_rows": stock_rows,
            "stats": self.summary(),
        }

    def collect_minutes(self, trade_date: date, stock_codes: list[str]) -> dict[str, list[Any]]:
        self._ensure_provider()
        result = {}
        for code in stock_codes[:int(self.config.get("minute_max_stocks", 30))]:
            self.stats["minute"] += 1
            try:
                rows = self.provider.get_minute_bars(code, f"{trade_date.isoformat()} 10:30:00", f"{trade_date.isoformat()} 11:30:00", "1m")
            except IFindHttpCallLimitReached:
                raise
            except IFindHttpError as exc:
                if exc.category in {
                    IFindHttpErrorCategory.ACCESS_TOKEN_INVALID,
                    IFindHttpErrorCategory.ACCESS_TOKEN_EXPIRED,
                    IFindHttpErrorCategory.DEVICE_LIMIT_EXCEEDED,
                    IFindHttpErrorCategory.NOT_AUTHORIZED,
                    IFindHttpErrorCategory.ACCOUNT_EXPIRED,
                    IFindHttpErrorCategory.RATE_LIMITED,
                }:
                    raise
                self.stats["minute_failures"] += 1
                self.failures.append({"stock_code": normalize_ts_code(code), "category": exc.category.value})
                result[normalize_ts_code(code)] = []
                continue
            result[normalize_ts_code(code)] = rows
            for row in rows:
                self._persist_minute(row)
        self.session.commit()
        self._sync_stats()
        return result

    def load_morning_snapshots(self, trade_date: date, stock_codes: list[str], *, cutoff: clock_time = clock_time(11, 30)) -> dict[str, Any]:
        """Reuse only already persisted morning snapshots for point-in-time validation."""
        values = self.session.scalars(select(MarketSnapshotShadow).where(
            MarketSnapshotShadow.stock_code.in_([normalize_ts_code(code) for code in stock_codes]),
            MarketSnapshotShadow.provider == "IFIND_HTTP",
        ).order_by(MarketSnapshotShadow.snapshot_time.desc())).all()
        result: dict[str, Any] = {}
        for row in values:
            stamp = row.snapshot_time
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=SHANGHAI)
            local = stamp.astimezone(SHANGHAI)
            code = normalize_ts_code(row.stock_code)
            if local.date() == trade_date and local.time().replace(tzinfo=None) <= cutoff and code not in result:
                result[code] = row
        self.stats["cache_hits"] += len(result)
        return result

    def summary(self) -> dict[str, Any]:
        self._sync_stats()
        total = self.stats["index"] + self.stats["snapshot"] + self.stats["minute"] + self.stats["retries"]
        return {
            **self.stats,
            "total": total,
            "hard_limit": self.authorized_call_limit,
            "failures": list(self.failures),
        }

    def _ensure_provider(self) -> None:
        if self.provider is not None:
            return
        gate = self.gate()
        if not gate["passed"]:
            raise RuntimeError("BLOCKED_IFIND_UNAVAILABLE")
        shadow = self.app_config.config_files.get("ifind_shadow", {}).get("ifind_shadow", {})
        self.auth = IFindHttpAuthManager(
            base_url=os.getenv("IFIND_HTTP_BASE_URL", "https://quantapi.51ifind.com/api/v1"),
            timeout_seconds=float(shadow.get("timeout_seconds", 30)),
        )
        self.client = IFindHttpClient(
            self.auth,
            maximum_calls=self.authorized_call_limit,
            authorized_maximum_calls=self.authorized_call_limit,
            interval_ms=int(self.config.get("minimum_call_interval_ms", 1000)),
        )
        self.provider = IFindHttpP0Provider(self.client, enabled=True, cache_ttl_seconds=15)

    def _index_codes(self) -> list[str]:
        values = self.app_config.config_files.get("market_review", {}).get("market_review", {}).get("indices", [])
        return [normalize_ts_code(item["index_code"]) for item in values if item.get("index_code")][:int(self.config.get("index_max_codes", 8))]

    def _persist_snapshot(self, code: str, row: Any, *, purpose: str) -> None:
        now = datetime.now(SHANGHAI)
        stamp = _parse_time(getattr(row, "datetime", None), now)
        code = normalize_ts_code(code)
        existing = self.session.scalar(select(MarketSnapshotShadow).where(
            MarketSnapshotShadow.stock_code == code,
            MarketSnapshotShadow.snapshot_time == stamp,
            MarketSnapshotShadow.provider == "IFIND_HTTP",
        ))
        if existing:
            self.stats["cache_hits"] += 1
            return
        payload = row.model_dump(mode="json") if hasattr(row, "model_dump") else dict(row)
        self.session.add(MarketSnapshotShadow(
            stock_code=code,
            stock_name=getattr(row, "stock_name", None) or getattr(row, "index_name", None),
            snapshot_time=stamp,
            provider_time=getattr(row, "provider_time", None) or getattr(row, "datetime", None),
            latest=_number(getattr(row, "latest", None)), open=_number(getattr(row, "open", None)),
            high=_number(getattr(row, "high", None)), low=_number(getattr(row, "low", None)),
            pre_close=_number(getattr(row, "pre_close", None)), change_percent=_number(getattr(row, "change_percent", None)),
            volume=_number(getattr(row, "volume", None)), amount=_number(getattr(row, "amount", None)),
            limit_up=_number(getattr(row, "limit_up", None)), limit_down=_number(getattr(row, "limit_down", None)),
            observed_delay_seconds=_number(getattr(row, "observed_delay_seconds", None)),
            market_session=getattr(row, "market_session", None) or "MIDDAY_BREAK",
            data_status=getattr(row, "data_status", "REALTIME"), provider="IFIND_HTTP", transport="HTTP",
            fetched_at=now, purpose=purpose,
            response_hash=hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest(),
        ))

    def _persist_minute(self, row: Any) -> None:
        now = datetime.now(SHANGHAI)
        stamp = _parse_time(getattr(row, "datetime", None), now)
        code = normalize_ts_code(row.stock_code)
        existing = self.session.scalar(select(MarketMinuteBarShadow).where(
            MarketMinuteBarShadow.stock_code == code,
            MarketMinuteBarShadow.bar_time == stamp,
            MarketMinuteBarShadow.interval == "1m",
            MarketMinuteBarShadow.provider == "IFIND_HTTP",
        ))
        if existing:
            self.stats["cache_hits"] += 1
            return
        self.session.add(MarketMinuteBarShadow(
            stock_code=code, bar_time=stamp, interval="1m",
            open=_number(row.open), high=_number(row.high), low=_number(row.low), close=_number(row.close),
            volume=_number(row.volume), amount=_number(row.amount), change_percent=_number(getattr(row, "change_percent", None)),
            provider="IFIND_HTTP", data_status=row.data_status, fetched_at=now, purpose="MIDDAY_MINUTE",
        ))

    def _sync_stats(self) -> None:
        if self.auth is not None:
            self.stats["auth"] = int(getattr(self.auth, "auth_calls", 0))


def _batches(items: list[str], size: int) -> Iterable[list[str]]:
    for offset in range(0, len(items), max(1, size)):
        yield items[offset:offset + max(1, size)]


def _parse_time(value: str | None, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI)).astimezone(SHANGHAI)
    except (TypeError, ValueError):
        return fallback


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
