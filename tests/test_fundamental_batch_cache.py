from __future__ import annotations

from datetime import datetime, timedelta, timezone

from datasource.tushare_provider import TushareEndpointResult
from fundamentals.cache import ReportPeriodCache, SCHEMA_VERSION
from fundamentals.tushare_service import TushareFundamentalBatchService


class FakeProvider:
    per_stock_api_call_count = 0

    def __init__(self):
        self.calls = []

    def get_fundamental_period_batch(self, interface, period, *, mainbz_type=None, use_cache=False):
        self.calls.append((interface, period, mainbz_type))
        return TushareEndpointResult(api_name=interface, status="available", records=[{"ts_code": "000001.SZ"}])


def test_period_cache_metadata_hit_and_atomic_write(tmp_path):
    provider = FakeProvider()
    cache = ReportPeriodCache(tmp_path)
    service = TushareFundamentalBatchService(provider=provider, cache=cache)
    first = service.prewarm(["20250331"], ["income"], dry_run=False)
    second = service.prewarm(["20250331"], ["income"], dry_run=False)
    assert first["per_stock_api_call_count"] == 0
    assert second["results"][0]["cache_status"] == "HIT"
    assert len(provider.calls) == 1
    payload = cache.read("income", "20250331", {"period": "20250331"})
    assert payload["metadata"]["schema_version"] == SCHEMA_VERSION
    assert payload["metadata"]["row_count"] == 1
    assert not list(tmp_path.rglob("*.tmp"))


def test_period_cache_stale_detection_and_forbids_per_stock(tmp_path):
    cache = ReportPeriodCache(tmp_path, ttl_hours=1)
    payload = {"metadata": {"fetched_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}}
    assert cache.is_stale(payload)
    try:
        cache.fetch("income", "20250331", {"ts_code": "000001.SZ"}, lambda: None)
    except ValueError as exc:
        assert "per-stock" in str(exc)
    else:
        raise AssertionError("per-stock report cache call must be rejected")
