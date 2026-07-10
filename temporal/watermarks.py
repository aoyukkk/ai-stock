from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from backend.core.config import CONFIG_DIR
from temporal.schemas import DatasetWatermark


class DatasetWatermarkService:
    def __init__(self, cache_root: Path | str = "data/cache/tushare", expected_count: int | None = None) -> None:
        self.cache_root = Path(cache_root)
        self.expected_codes = self._expected_universe_codes()
        self.expected_count = expected_count or len(self.expected_codes)
        policy = yaml.safe_load((CONFIG_DIR / "temporal.yaml").read_text(encoding="utf-8"))
        self.thresholds = policy["dataset_update_policy"]["completeness_thresholds"]

    def trade_date_watermark(self, dataset: str, requested: date) -> DatasetWatermark:
        path = self.cache_root / "trade_date" / dataset / f"{requested.strftime('%Y%m%d')}.json"
        records = self._read_records(path)
        latest = self._latest_trade_date(records)
        raw_count = len(records)
        actual_codes = [str(row.get("ts_code")) for row in records if row.get("ts_code")]
        actual_set = set(actual_codes)
        row_count = len(actual_set) or raw_count
        expected = self.expected_count
        duplicate_count = max(0, len(actual_codes) - len(actual_set))
        unexpected_count = len(actual_set - self.expected_codes) if self.expected_codes else 0
        missing_count = len(self.expected_codes - actual_set) if self.expected_codes else max(0, expected - row_count)
        reasons: dict[str, int] = {key: 0 for key in (
            "SUSPENDED", "NO_TRADE", "NEW_LISTING", "DELISTED", "ST_STATUS",
            "DATASET_NOT_APPLICABLE", "PROVIDER_MISSING", "DUPLICATE_RECORD", "UNKNOWN_MISSING",
        )}
        reasons["DUPLICATE_RECORD"] = duplicate_count
        reasons["DATASET_NOT_APPLICABLE"] = unexpected_count
        reasons["UNKNOWN_MISSING"] = missing_count
        ratio = min(1.0, row_count / expected) if expected else 0.0
        threshold = float(self.thresholds.get(dataset, 1.0))
        is_complete = latest == requested and ratio >= threshold
        fetched = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if path.exists() else datetime.now(timezone.utc)
        factor_available = fetched if dataset == "adj_factor" and records else None
        return DatasetWatermark(
            dataset_name=dataset, requested_trade_date=requested, latest_trade_date=latest,
            fetched_at=fetched, row_count=row_count, expected_count=expected,
            excluded_count=missing_count, raw_actual_count=raw_count,
            unique_stock_count=row_count, expected_stock_count=expected,
            duplicate_count=duplicate_count, unexpected_stock_count=unexpected_count,
            excluded_stock_count=missing_count, exclusion_reason_counts=reasons,
            coverage_ratio=ratio,
            is_complete=is_complete, is_stale=latest != requested,
            schema_version="trade-date-cache-v1", cache_key=str(path),
            source_status="CACHE" if path.exists() else "MISSING",
            error_category=None if path.exists() else "CACHE_MISS",
            factor_available_at=factor_available,
            adjustment_mode="UNADJUSTED_WITH_FACTOR_REFERENCE" if dataset == "adj_factor" else None,
            adjusted_price_series_version="point-in-time-v1" if factor_available else None,
        )

    def latest_cached_trade_date(self, dataset: str) -> date | None:
        root = self.cache_root / "trade_date" / dataset
        values = []
        if root.exists():
            for path in root.glob("*.json"):
                stem = path.stem
                if len(stem) == 8 and stem.isdigit():
                    values.append(datetime.strptime(stem, "%Y%m%d").date())
        return max(values) if values else None

    @staticmethod
    def _read_records(path: Path) -> list[dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else list(value.get("records", []))

    @staticmethod
    def _latest_trade_date(records: list[dict[str, Any]]) -> date | None:
        values = [str(row.get("trade_date") or "") for row in records]
        valid = [datetime.strptime(value, "%Y%m%d").date() for value in values if len(value) == 8 and value.isdigit()]
        return max(valid) if valid else None

    def _expected_universe_codes(self) -> set[str]:
        root = self.cache_root / "fundamental" / "stock_basic"
        candidates = sorted(root.rglob("*.json"), reverse=True) if root.exists() else []
        for path in candidates:
            records = self._read_records(path)
            if records:
                return {str(row.get("ts_code")) for row in records if row.get("ts_code")}
        return set()
