from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core.runtime_paths import tushare_cache_root


SCHEMA_VERSION = "fundamental-cache-v1"


class ReportPeriodCache:
    def __init__(self, root: Path | str | None = None, ttl_hours: int = 24) -> None:
        self.root = Path(root) if root is not None else tushare_cache_root() / "fundamental"
        self.ttl = timedelta(hours=ttl_hours)

    def path_for(self, interface: str, period: str, params: dict[str, Any] | None = None) -> Path:
        safe_params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        digest = hashlib.sha256(json.dumps(safe_params, sort_keys=True).encode()).hexdigest()[:16]
        return self.root / interface / period / f"{digest}.json"

    def _read_raw(self, interface: str, period: str, params: dict[str, Any] | None = None) -> dict | None:
        path = self.path_for(interface, period, params)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if payload.get("metadata", {}).get("schema_version") != SCHEMA_VERSION:
            return None
        return payload

    def read(self, interface: str, period: str, params: dict[str, Any] | None = None) -> dict | None:
        """Return only policy-current cache entries.

        Callers that intentionally display stale evidence must use
        ``read_with_freshness`` so staleness cannot be silently erased.
        """
        payload = self._read_raw(interface, period, params)
        return None if payload is None or self.is_stale(payload) else payload

    def read_with_freshness(
        self,
        interface: str,
        period: str,
        params: dict[str, Any] | None = None,
        *,
        now: datetime | None = None,
        allow_stale: bool = False,
    ) -> tuple[dict | None, dict[str, Any]]:
        payload = self._read_raw(interface, period, params)
        if payload is None:
            return None, {
                "freshness_status": "MISSING",
                "cache_fetched_at": None,
                "cache_age_hours": None,
            }
        fetched = datetime.fromisoformat(payload["metadata"]["fetched_at"])
        current = now or datetime.now(timezone.utc)
        age_hours = max(0.0, (current.astimezone(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds() / 3600)
        stale = self.is_stale(payload, current)
        evidence = {
            "freshness_status": "STALE" if stale else "FRESH",
            "cache_fetched_at": fetched.isoformat(),
            "cache_age_hours": age_hours,
        }
        return (payload if allow_stale or not stale else None), evidence

    def is_stale(self, payload: dict, now: datetime | None = None) -> bool:
        fetched = datetime.fromisoformat(payload["metadata"]["fetched_at"])
        return (now or datetime.now(timezone.utc)) - fetched > self.ttl

    def fetch(
        self,
        interface: str,
        period: str,
        params: dict[str, Any],
        loader: Callable[[], Any],
        *,
        force: bool = False,
    ) -> dict:
        if params.get("ts_code"):
            raise ValueError("report-period cache forbids per-stock API calls")
        cached = self._read_raw(interface, period, params)
        if cached is not None and not force and not self.is_stale(cached):
            cached["metadata"]["cache_status"] = "HIT"
            return cached
        result = loader()
        records = list(getattr(result, "records", []))
        status = str(getattr(result, "status", "error"))
        now = datetime.now(timezone.utc)
        normalized_params = {k: v for k, v in params.items() if v not in (None, "")}
        payload = {
            "metadata": {
                "interface": interface,
                "period": period,
                "parameters_hash": hashlib.sha256(
                    json.dumps(normalized_params, sort_keys=True).encode()
                ).hexdigest(),
                "fetched_at": now.isoformat(),
                "row_count": len(records),
                "status": status,
                "schema_version": SCHEMA_VERSION,
                "cache_status": "REFRESHED" if cached else "MISS",
            },
            "records": records,
        }
        self._atomic_write(self.path_for(interface, period, params), payload)
        return payload

    @staticmethod
    def _atomic_write(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
