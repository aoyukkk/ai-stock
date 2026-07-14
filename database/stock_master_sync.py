from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models.stock import StockMaster
from stock_codes import normalize_ts_code
from backend.core.runtime_paths import tushare_cache_root


DEFAULT_CACHE_ROOT = tushare_cache_root() / "fundamental" / "stock_basic"


class StockMasterSyncError(RuntimeError):
    pass


class ManualStockResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class StockMasterSyncResult:
    source_path: str
    rows_read: int
    rows_inserted: int
    rows_updated: int
    rows_skipped: int
    source_duplicate_count: int
    per_stock_api_call_count: int = 0
    llm_call_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ManualStockResolution:
    input_name: str
    canonical_name: str
    ts_code: str
    exchange: str
    listing_status: str
    match_method: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class StockMasterSyncService:
    def __init__(self, session: Session, cache_root: Path | str = DEFAULT_CACHE_ROOT) -> None:
        self.session = session
        self.cache_root = Path(cache_root)

    def latest_cache_path(self) -> Path:
        candidates = sorted(
            self.cache_root.rglob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if self.cache_root.exists() else []
        for path in candidates:
            payload = _read_payload(path)
            metadata = payload.get("metadata", {})
            if (
                metadata.get("interface") == "stock_basic"
                and metadata.get("status") == "available"
                and isinstance(payload.get("records"), list)
            ):
                return path
        raise StockMasterSyncError("STOCK_BASIC_CACHE_NOT_AVAILABLE")

    def sync_from_cache(self, *, commit: bool = True) -> StockMasterSyncResult:
        source_path = self.latest_cache_path()
        records = _read_payload(source_path).get("records", [])
        existing = {
            row.code: row
            for row in self.session.scalars(select(StockMaster)).all()
        }
        inserted = 0
        updated = 0
        skipped = 0
        duplicate_count = 0
        seen: set[str] = set()
        now = datetime.now(timezone.utc)

        try:
            for record in records:
                normalized = _normalize_record(record)
                if normalized is None:
                    skipped += 1
                    continue
                ts_code = normalized["code"]
                if ts_code in seen:
                    duplicate_count += 1
                    skipped += 1
                    continue
                seen.add(ts_code)
                row = existing.get(ts_code)
                if row is None:
                    self.session.add(StockMaster(**normalized, created_at=now, updated_at=now))
                    inserted += 1
                    continue
                changed = False
                for field, value in normalized.items():
                    if getattr(row, field) != value:
                        setattr(row, field, value)
                        changed = True
                if changed:
                    row.updated_at = now
                    updated += 1
                else:
                    skipped += 1
            if commit:
                self.session.commit()
            else:
                self.session.flush()
        except Exception:
            self.session.rollback()
            raise

        return StockMasterSyncResult(
            source_path=str(source_path.resolve()),
            rows_read=len(records),
            rows_inserted=inserted,
            rows_updated=updated,
            rows_skipped=skipped,
            source_duplicate_count=duplicate_count,
        )

    def row_count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(StockMaster)) or 0)

    def duplicate_code_count(self) -> int:
        duplicate_groups = self.session.execute(
            select(StockMaster.code)
            .group_by(StockMaster.code)
            .having(func.count(StockMaster.id) > 1)
        ).all()
        return len(duplicate_groups)


class ManualStockResolver:
    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve(self, input_name: str) -> ManualStockResolution:
        name = str(input_name or "").strip()
        if not name:
            raise ManualStockResolutionError("MANUAL_STOCK_NAME_REQUIRED")
        matches = self.session.scalars(
            select(StockMaster).where(StockMaster.name == name)
        ).all()
        if not matches:
            raise ManualStockResolutionError(f"MANUAL_STOCK_NOT_FOUND:{name}")
        if len(matches) != 1:
            raise ManualStockResolutionError(f"MANUAL_STOCK_AMBIGUOUS:{name}")
        row = matches[0]
        ts_code = normalize_ts_code(row.code)
        status = str(row.status or "UNKNOWN").upper()
        if status not in {"L", "LISTED", "NORMAL"}:
            raise ManualStockResolutionError(f"MANUAL_STOCK_NOT_LISTED:{name}")
        return ManualStockResolution(
            input_name=name,
            canonical_name=row.name,
            ts_code=ts_code,
            exchange=ts_code.rsplit(".", 1)[1],
            listing_status=status,
            match_method="EXACT_NAME",
        )

    def resolve_all(self, names: list[str]) -> list[ManualStockResolution]:
        if len(set(names)) != len(names):
            raise ManualStockResolutionError("MANUAL_STOCK_INPUT_DUPLICATE")
        return [self.resolve(name) for name in names]


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise StockMasterSyncError(f"STOCK_BASIC_CACHE_INVALID:{path.name}") from exc
    if not isinstance(payload, dict):
        raise StockMasterSyncError(f"STOCK_BASIC_CACHE_INVALID:{path.name}")
    return payload


def _normalize_record(record: dict[str, Any]) -> dict[str, Any] | None:
    raw_code = record.get("ts_code") or record.get("symbol")
    name = str(record.get("name") or "").strip()
    if not raw_code or not name:
        return None
    try:
        ts_code = normalize_ts_code(str(raw_code))
        list_date = _parse_date(record.get("list_date"))
    except ValueError:
        return None
    exchange = str(record.get("exchange") or ts_code.rsplit(".", 1)[1]).upper()
    status = str(record.get("list_status") or "L").upper()
    return {
        "code": ts_code,
        "name": name,
        "market": exchange,
        "industry": str(record.get("industry") or "").strip() or None,
        "list_date": list_date,
        "status": status,
    }


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip().replace("-", "")
    if not text:
        return None
    if len(text) != 8 or not text.isdigit():
        raise ValueError("INVALID_LIST_DATE")
    return datetime.strptime(text, "%Y%m%d").date()
