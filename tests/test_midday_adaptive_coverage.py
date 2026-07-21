from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from datasource.ifind.http.normalizer import normalize_rows_with_audit
from midday.adaptive_coverage import AdaptiveCoverageResolver, dynamic_run_call_budget, market_bucket


def test_large_paired_table_shape_preserves_every_security_code():
    payload = {
        "tables": {
            "thscode": ["600000.SH", "000001.SZ"],
            "table": [
                {"time": ["2026-07-20 11:30:00"], "latest": [10.0], "volume": [None]},
                {"time": ["2026-07-20 11:30:00"], "latest": [11.0], "volume": [100]},
            ],
        }
    }
    rows, audit = normalize_rows_with_audit(payload)
    assert [row["thscode"] for row in rows] == ["600000.SH", "000001.SZ"]
    assert rows[0]["volume"] is None
    assert "PAIRED_CODE_TABLE_ARRAY" in audit["parser_branch_used"]
    assert audit["raw_security_code_array"] == ["600000.SH", "000001.SZ"]


def test_multiple_tables_are_all_parsed_and_null_rows_are_not_dropped():
    payload = {
        "tables": [
            {"thscode": "600000.SH", "table": {"time": ["2026-07-20 11:30:00"], "latest": [10]}},
            {"thscode": "000001.SZ", "table": {"time": ["2026-07-20 11:30:00"], "latest": [None]}},
        ]
    }
    rows, audit = normalize_rows_with_audit(payload)
    assert len(rows) == 2
    assert rows[1]["latest"] is None
    assert audit["raw_table_count"] >= 2


@pytest.mark.parametrize(
    ("code", "bucket"),
    [
        ("600000.SH", "SH_MAIN"),
        ("688001.SH", "STAR"),
        ("000001.SZ", "SZ_MAIN"),
        ("300001.SZ", "CHINEXT"),
        ("830001.BJ", "BSE"),
    ],
)
def test_market_grouping(code, bucket):
    assert market_bucket(code) == bucket


def test_dynamic_budget_is_run_scoped_and_capped_at_400():
    audit = dynamic_run_call_budget(5291, reliable_batch_size=20)
    assert audit["run_call_budget"] == 400
    assert audit["estimated_calls"] > 30


class PartialClient:
    def __init__(self):
        self.call_count = 0
        self.snapshot_requests: list[list[str]] = []
        self.successful: set[str] = set()

    def post(self, endpoint, payload):
        assert endpoint == "snap_shot"
        codes = payload["codes"].split(",")
        assert not (set(codes) & self.successful)
        self.snapshot_requests.append(codes)
        self.call_count += 1
        returned = codes[::2] if len(codes) > 1 else codes
        self.successful.update(returned)
        rows = [
            {
                "thscode": code,
                "time": "2026-07-20 11:30:00",
                "preClose": 10,
                "open": 10,
                "high": 11,
                "low": 9,
                "latest": 10,
                "volume": 12100,
                "amount": 121000,
            }
            for code in returned
        ]
        return SimpleNamespace(payload={"tables": rows})


class PartialProvider:
    def __init__(self):
        self.client = PartialClient()

    def get_minute_bars_batch(self, codes, start, end, interval):
        self.client.call_count += 1
        base = datetime(2026, 7, 20, 9, 30)
        output = {}
        for code in codes:
            bars = []
            for index in range(121):
                stamp = base + timedelta(minutes=index)
                bars.append(SimpleNamespace(
                    stock_code=code,
                    datetime=stamp.strftime("%Y-%m-%d %H:%M:%S"),
                    open=10,
                    high=11 if index == 120 else 10,
                    low=9 if index == 0 else 10,
                    close=10,
                    volume=100,
                    amount=1000,
                    data_status="AVAILABLE",
                ))
            output[code] = bars
        return output


class EmptySnapshotClient(PartialClient):
    def post(self, endpoint, payload):
        codes = payload["codes"].split(",")
        assert not (set(codes) & self.successful)
        self.snapshot_requests.append(codes)
        self.call_count += 1
        return SimpleNamespace(payload={"errorcode": 0, "tables": []})


class EmptySnapshotProvider(PartialProvider):
    def __init__(self):
        self.client = EmptySnapshotClient()


def test_partial_batches_split_only_missing_and_single_fallback_completes():
    provider = PartialProvider()
    config = {
        "snapshot_batch_size": 100,
        "minute_reconstruction_batch_size": 60,
        "maximum_provider_calls": 120,
        "validation_sample_count": 2,
        "minimum_full_a_coverage": 0.98,
    }
    codes = [f"00000{index}.SZ" for index in range(1, 7)]
    resolver = AdaptiveCoverageResolver(provider, config, diagnostic_single_limit=2)
    result = resolver.resolve(
        date(2026, 7, 20), time(11, 30), codes, previous_closes={code: 10 for code in codes}
    )
    assert result.returned == len(codes)
    assert result.coverage == 1
    assert resolver.single_code_fallback_count >= 1
    assert resolver.adaptive_split_count >= 1
    assert not resolver.unresolved
    assert all(value >= 1 for value in resolver.attempts.values())


def test_market_without_single_snapshot_support_switches_to_batched_minutes():
    provider = EmptySnapshotProvider()
    config = {
        "snapshot_batch_size": 100,
        "minute_reconstruction_batch_size": 20,
        "maximum_provider_calls": 120,
        "validation_sample_count": 2,
        "minimum_full_a_coverage": 0.98,
    }
    codes = [f"600{index:03d}.SH" for index in range(25)]
    resolver = AdaptiveCoverageResolver(provider, config, diagnostic_single_limit=8)
    result = resolver.resolve(
        date(2026, 7, 20), time(11, 30), codes, previous_closes={code: 10 for code in codes}
    )
    assert result.returned == 25
    assert resolver.minute_fallback_count == 2
    assert resolver.single_code_fallback_count == 8
    assert all(source == "IFIND_HIGH_FREQUENCY_1M_BATCH" for source in resolver.sources.values())
