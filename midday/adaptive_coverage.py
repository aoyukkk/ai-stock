from __future__ import annotations

import math
import random
import time
from collections import Counter, defaultdict
from datetime import date, datetime, time as clock_time
from typing import Any, Callable

from datasource.ifind.http.errors import IFindHttpError
from datasource.ifind.http.normalizer import normalize_rows_with_audit
from midday.asof import AsOfMarketDataResolver
from midday.core import SHANGHAI
from midday.full_a_data import FullAAsOfResolver, FullAAsOfResult, FullADataFreshnessError
from stock_codes import normalize_ts_code


PROBE_SIZES = (20, 40, 60, 80, 100)
VALID_SNAPSHOT_FIELDS = ("open", "high", "low", "latest", "volume", "amount")


def market_bucket(code: str) -> str:
    normalized = normalize_ts_code(code)
    number, suffix = normalized.split(".", 1)
    if suffix == "BJ":
        return "BSE"
    if suffix == "SH":
        return "STAR" if number.startswith("688") else "SH_MAIN"
    return "CHINEXT" if number.startswith(("300", "301")) else "SZ_MAIN"


def dynamic_run_call_budget(
    eligible_count: int,
    *,
    reliable_batch_size: int = 20,
    missing_retry_estimate: int | None = None,
    index_calls: int = 1,
    top50_minute_calls: int = 1,
    safety_margin: int = 10,
    authorized_maximum: int = 400,
) -> dict[str, int]:
    reliable = max(1, int(reliable_batch_size))
    retry_estimate = (
        int(missing_retry_estimate)
        if missing_retry_estimate is not None
        else math.ceil(eligible_count / reliable * 0.25)
    )
    estimated = (
        math.ceil(eligible_count / reliable)
        + retry_estimate
        + int(index_calls)
        + int(top50_minute_calls)
        + int(safety_margin)
    )
    budget = min(max(math.ceil(estimated * 1.25) + 10, 120), int(authorized_maximum))
    return {
        "eligible_count": int(eligible_count),
        "planning_reliable_batch_size": reliable,
        "missing_retry_estimate": retry_estimate,
        "index_calls": int(index_calls),
        "top50_minute_calls": int(top50_minute_calls),
        "safety_margin": int(safety_margin),
        "estimated_calls": estimated,
        "run_call_budget": budget,
        "authorized_maximum": int(authorized_maximum),
    }


class AdaptiveCoverageResolver(FullAAsOfResolver):
    """Per-market snapshot reconciliation with split and single-code fallbacks."""

    def __init__(
        self,
        provider: Any,
        config: dict[str, Any],
        *,
        sdk_snapshot: Callable[[list[str], str], dict[str, Any] | None] | None = None,
        now: Callable[[], datetime] | None = None,
        diagnostic_single_limit: int = 20,
    ) -> None:
        super().__init__(provider, config, now=now)
        self.sdk_snapshot = sdk_snapshot
        self.maximum_calls = int(config["maximum_provider_calls"])
        self.diagnostic_single_limit = max(0, int(diagnostic_single_limit))
        self.external_calls = 0
        self.sdk_calls = 0
        self.adaptive_split_count = 0
        self.single_code_fallback_count = 0
        self.minute_fallback_count = 0
        self.attempts: defaultdict[str, int] = defaultdict(int)
        self.sources: dict[str, str] = {}
        self.unresolved: dict[str, str] = {}
        self.batch_audits: list[dict[str, Any]] = []
        self.probes: list[dict[str, Any]] = []
        self.reliable_by_market: dict[str, int] = {}
        self.market_distribution: dict[str, dict[str, int]] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._minute_series: dict[str, list[dict[str, Any]]] = {}
        self._previous: dict[str, float | None] = {}
        self._trade_date: date | None = None
        self._cutoff: clock_time | None = None
        self._diagnosed: set[str] = set()
        self._diagnostic_outcomes: defaultdict[str, list[bool]] = defaultdict(list)
        self.post_cutoff_rows_excluded = 0

    @property
    def coverage_audit(self) -> dict[str, Any]:
        attempts = dict(sorted(self.attempts.items()))
        sources = dict(sorted(self.sources.items()))
        provider_missing = sum(max(0, row.get("requested_code_count", 0) - row.get("raw_response_code_count", 0)) for row in self.batch_audits)
        parser_lost = sum(max(0, row.get("raw_response_code_count", 0) - row.get("parser_output_code_count", 0)) for row in self.batch_audits)
        diagnostic_successes = sum(source == "HTTP_SINGLE_DIAGNOSTIC" for source in self.sources.values())
        return {
            "response_audits": self.batch_audits,
            "batch_probes": self.probes,
            "exchange_board_distribution": self.market_distribution,
            "reliable_complete_batch_size": min(self.reliable_by_market.values(), default=0),
            "reliable_complete_batch_size_by_market": self.reliable_by_market,
            "adaptive_split_count": self.adaptive_split_count,
            "single_code_fallback_count": self.single_code_fallback_count,
            "sdk_fallback_count": self.sdk_calls,
            "minute_fallback_count": self.minute_fallback_count,
            "per_code_attempts": attempts,
            "per_code_final_source": sources,
            "unresolved_codes": dict(sorted(self.unresolved.items())),
            "provider_calls": self.external_calls,
            "http_calls": int(getattr(self.provider.client, "call_count", 0)),
            "successful_calls": int(getattr(self.provider.client, "success_count", 0)),
            "failed_calls": int(getattr(self.provider.client, "failed_count", 0)),
            "retry_calls": 0,
            "sdk_calls": self.sdk_calls,
            "concurrency": 1,
            "qps_limit": 1,
            "probe_sizes": list(PROBE_SIZES),
            "eligible_bar_range": ["09:30:00", "11:30:00"],
            "post_cutoff_rows_excluded": self.post_cutoff_rows_excluded,
            "raw_response_parser_diagnosis": {
                "provider_missing_code_observations": provider_missing,
                "parser_lost_code_observations": parser_lost,
                "single_code_diagnostic_sample": len(self._diagnosed),
                "single_code_diagnostic_successes": diagnostic_successes,
                "classification": "PARSER_LOSS_DETECTED" if parser_lost else "PROVIDER_OR_BATCH_OMISSION",
            },
        }

    def resolve(
        self,
        trade_date: date,
        cutoff: clock_time,
        codes: list[str],
        *,
        previous_closes: dict[str, float | None],
    ) -> FullAAsOfResult:
        normalized = list(dict.fromkeys(normalize_ts_code(code) for code in codes))
        self._previous = {normalize_ts_code(code): value for code, value in previous_closes.items()}
        self._trade_date, self._cutoff = trade_date, cutoff
        grouped: dict[str, list[str]] = {name: [] for name in ("SH_MAIN", "STAR", "SZ_MAIN", "CHINEXT", "BSE")}
        for code in normalized:
            grouped[market_bucket(code)].append(code)

        for market, market_codes in grouped.items():
            if not market_codes:
                self.market_distribution[market] = {"requested": 0, "returned": 0, "missing": 0}
                continue
            self._resolve_market(market, market_codes)
            returned = sum(code in self._snapshots for code in market_codes)
            self.market_distribution[market] = {
                "requested": len(market_codes),
                "returned": returned,
                "missing": len(market_codes) - returned,
            }

        missing = [code for code in normalized if code not in self._snapshots]
        for code in missing:
            self.unresolved.setdefault(code, "UNRESOLVED_PROVIDER_DATA")

        validation_count = min(int(self.config.get("validation_sample_count", 20)), len(self._snapshots))
        validation_codes = self._stratified_sample(sorted(self._snapshots), validation_count)
        validation = self._validate_snapshot_semantics(
            trade_date, cutoff, validation_codes, self._snapshots, self._previous
        )
        valid = {
            code: row
            for code, row in self._snapshots.items()
            if row.get("data_quality") in {"VALID_EXACT", "VALID_RECONSTRUCTED"}
            and row.get("cutoff_compliant")
        }
        coverage = len(valid) / len(normalized) if normalized else 0.0
        minimum = float(self.config.get("minimum_full_a_coverage", 0.98))
        if coverage < minimum:
            raise FullADataFreshnessError(
                f"FULL_A_DATA_COVERAGE_FAILED:coverage={coverage:.6f}:minimum={minimum:.6f}:"
                f"unresolved={len(normalized) - len(valid)}"
            )
        methods = {row["reconstruction_method"] for row in valid.values()}
        method = (
            "ADAPTIVE_MARKET_SNAPSHOT_RECONCILIATION"
            if methods == {"IFIND_HISTORICAL_SNAPSHOT"}
            else "ADAPTIVE_SNAPSHOT_AND_1M_RECONSTRUCTION"
        )
        self.audit.extend(self.batch_audits)
        return FullAAsOfResult(
            valid,
            self._minute_series,
            method,
            validation,
            self.audit,
            len(normalized),
            len(valid),
            coverage,
            self.external_calls,
            self.hf_batches,
            self.post_cutoff_rows_excluded,
        )

    def _validate_snapshot_semantics(self, trade_date, cutoff, codes, snapshots, previous_closes):
        if codes:
            self._reserve_call(list(codes))
        result = super()._validate_snapshot_semantics(
            trade_date, cutoff, codes, snapshots, previous_closes
        )
        if codes and not result.get("passed"):
            raise FullADataFreshnessError("FULL_A_DATA_FRESHNESS_FAILED:SNAPSHOT_SEMANTIC_VALIDATION")
        return result

    def fetch_minutes(
        self,
        trade_date: date,
        cutoff: clock_time,
        codes: list[str],
        *,
        maximum_additional_calls: int,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        normalized = list(dict.fromkeys(normalize_ts_code(code) for code in codes))
        output = {code: self._minute_series[code] for code in normalized if code in self._minute_series}
        remaining = [code for code in normalized if code not in output]
        size = max(1, int(self.config["minute_reconstruction_batch_size"]))
        needed = math.ceil(len(remaining) / size)
        if needed > maximum_additional_calls or self.external_calls + needed > self.maximum_calls:
            raise RuntimeError("FULL_A_TRIGGER_MINUTE_CAPACITY_FAILED")
        start = f"{trade_date.isoformat()} 09:30:00"
        asof = f"{trade_date.isoformat()} {cutoff.isoformat()}"
        for batch_id, offset in enumerate(range(0, len(remaining), size), 1):
            batch = remaining[offset : offset + size]
            begun = time.perf_counter()
            self._reserve_call(batch)
            values = self.provider.get_minute_bars_batch(batch, start, asof, "1m")
            self.hf_batches += 1
            rows = 0
            for code, bars in values.items():
                normalized_code = normalize_ts_code(code)
                output[normalized_code] = [
                    {
                        "bar_time": str(row.datetime),
                        "open": row.open,
                        "high": row.high,
                        "low": row.low,
                        "close": row.close,
                        "volume": row.volume,
                        "amount": row.amount,
                    }
                    for row in bars
                ]
                rows += len(bars)
            self.audit.append(self._audit("TRIGGER_MINUTE", batch_id, batch, rows, begun, None))
        return output, self.audit

    def _resolve_market(self, market: str, codes: list[str]) -> None:
        cursor = 0
        complete_sizes: list[int] = []
        probe_missing: list[str] = []
        for size in PROBE_SIZES:
            candidates = [code for code in codes[cursor:] if code not in self._snapshots]
            if len(candidates) < size:
                continue
            batch = candidates[:size]
            cursor += size
            result = self._request_http(batch, market=market, purpose=f"PROBE_{size}")
            complete = not result["missing_codes"] and not result["unexpected_codes"]
            self.probes.append({
                "market": market,
                "requested": size,
                "returned": result["normalized_output_code_count"],
                "complete": complete,
                "parser_branch_used": result["parser_branch_used"],
                "error_category": result.get("error_category"),
            })
            if complete:
                complete_sizes.append(size)
            else:
                probe_missing.extend(result["missing_codes"])
        # Preserve a raw 200-code structural sample where the board is large
        # enough.  It is diagnostic only and is not treated as a reliable
        # production batch size.
        candidates = [code for code in codes[cursor:] if code not in self._snapshots]
        if len(candidates) >= 200:
            batch = candidates[:200]
            cursor += 200
            result = self._request_http(batch, market=market, purpose="RAW_AUDIT_200")
            self.probes.append({
                "market": market,
                "requested": 200,
                "returned": result["normalized_output_code_count"],
                "complete": not result["missing_codes"] and not result["unexpected_codes"],
                "diagnostic_only": True,
                "parser_branch_used": result["parser_branch_used"],
                "error_category": result.get("error_category"),
            })
            probe_missing.extend(result["missing_codes"])
        reliable = max(complete_sizes, default=20)
        self.reliable_by_market[market] = reliable
        if not complete_sizes:
            self._diagnose_missing(list(dict.fromkeys(probe_missing)), market)
            remaining = [code for code in codes if code not in self._snapshots]
            outcomes = self._diagnostic_outcomes.get(market, [])
            if outcomes and not any(outcomes):
                self._minute_batch_fallback(remaining, market)
                return
        remaining = [code for code in codes if code not in self._snapshots]
        for offset in range(0, len(remaining), reliable):
            self._resolve_batch(remaining[offset : offset + reliable], market)

    def _resolve_batch(self, batch: list[str], market: str) -> None:
        pending = [code for code in batch if code not in self._snapshots]
        if not pending:
            return
        result = self._request_http(pending, market=market, purpose="ADAPTIVE_BATCH")
        missing = [code for code in result["missing_codes"] if code not in self._snapshots]
        if not missing:
            return
        if len(missing) == 1:
            self._single_fallback(missing[0], market)
            return
        self.adaptive_split_count += 1
        midpoint = len(missing) // 2
        self._resolve_batch(missing[:midpoint], market)
        self._resolve_batch(missing[midpoint:], market)

    def _diagnose_missing(self, missing: list[str], market: str) -> None:
        available = [code for code in missing if code not in self._diagnosed and code not in self._snapshots]
        remaining_slots = self.diagnostic_single_limit - len(self._diagnosed)
        if remaining_slots <= 0 or not available:
            return
        rng = random.Random(20260720)
        sample = rng.sample(available, min(8, remaining_slots, len(available)))
        for code in sample:
            self._diagnosed.add(code)
            self.single_code_fallback_count += 1
            result = self._request_http([code], market=market, purpose="SINGLE_DIAGNOSTIC")
            result["market_format_fallback"] = "CANONICAL_ACCEPTED_ALTERNATIVES_REJECTED_BY_ENDPOINT"
            if code not in self._snapshots and self.sdk_snapshot is not None and self.external_calls < self.maximum_calls:
                self._request_sdk([code], market=market)
            succeeded = code in self._snapshots
            self._diagnostic_outcomes[market].append(succeeded)
            if succeeded:
                self.sources[code] = "SINGLE_DIAGNOSTIC_HTTP_OR_SDK"

    def _single_fallback(self, code: str, market: str, *, diagnostic: bool = False) -> None:
        if code in self._snapshots:
            return
        self.single_code_fallback_count += 1
        result = self._request_http([code], market=market, purpose="SINGLE_NORMALIZED")
        if code in self._snapshots:
            if diagnostic:
                self.sources[code] = "HTTP_SINGLE_DIAGNOSTIC"
            return
        # Canonical iFinD market format is already NNNNNN.SH/SZ/BJ.  Record the
        # evaluated fallback without issuing a duplicate identical request.
        result["market_format_fallback"] = "SKIPPED_IDENTICAL_CANONICAL_FORMAT"
        if self.sdk_snapshot is not None and self.external_calls < self.maximum_calls:
            self._request_sdk([code], market=market)
        if code in self._snapshots:
            return
        self._minute_fallback(code, market)
        if code not in self._snapshots:
            self.unresolved[code] = "UNRESOLVED_PROVIDER_DATA"

    def _request_http(self, codes: list[str], *, market: str, purpose: str) -> dict[str, Any]:
        requested = [normalize_ts_code(code) for code in codes if normalize_ts_code(code) not in self._snapshots]
        if not requested:
            return self._empty_audit(codes, market, purpose)
        self._reserve_call(requested)
        begun = time.perf_counter()
        error_category = None
        payload: dict[str, Any] = {}
        try:
            response = self.provider.client.post(
                "snap_shot",
                {
                    "codes": ",".join(requested),
                    "indicators": "tradeDate,tradeTime,preClose,open,high,low,latest,volume,amount",
                    "starttime": self._asof,
                    "endtime": self._asof,
                },
            )
            payload = response.payload
        except IFindHttpError as exc:
            error_category = exc.category.value
        except Exception as exc:
            error_category = type(exc).__name__
        return self._reconcile(payload, requested, market, purpose, begun, error_category, "HTTP")

    def _request_sdk(self, codes: list[str], *, market: str) -> dict[str, Any]:
        requested = [code for code in codes if code not in self._snapshots]
        if not requested:
            return self._empty_audit(codes, market, "SDK_SINGLE")
        self._reserve_call(requested)
        self.sdk_calls += 1
        begun = time.perf_counter()
        payload: dict[str, Any] = {}
        error = None
        try:
            value = self.sdk_snapshot(requested, self._asof) if self.sdk_snapshot else None
            if isinstance(value, dict):
                payload = value
            else:
                error = "SDK_RESPONSE_SCHEMA_ERROR"
        except Exception as exc:
            error = type(exc).__name__
        return self._reconcile(payload, requested, market, "SDK_SINGLE", begun, error, "SDK")

    def _reconcile(
        self,
        payload: dict[str, Any],
        requested: list[str],
        market: str,
        purpose: str,
        begun: float,
        error_category: str | None,
        transport: str,
    ) -> dict[str, Any]:
        rows, structural = normalize_rows_with_audit(payload)
        parsed_codes: list[str] = []
        malformed = 0
        by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            raw_code = row.get("thscode") or row.get("code") or row.get("ts_code")
            if not raw_code:
                malformed += 1
                continue
            try:
                code = normalize_ts_code(raw_code)
            except ValueError:
                malformed += 1
                continue
            parsed_codes.append(code)
            by_code[code].append(row)
        requested_set = set(requested)
        unexpected = sorted(set(parsed_codes) - requested_set)
        null_only: list[str] = []
        for code in requested:
            candidates = by_code.get(code, [])
            valid_row = next((row for row in candidates if self._valid_snapshot_row(row)), None)
            if valid_row is None:
                if candidates and all(self._null_only(row) for row in candidates):
                    null_only.append(code)
                continue
            snapshot = self._snapshot_from_row(code, valid_row)
            if snapshot is not None:
                self._snapshots[code] = snapshot
                self.sources[code] = f"{transport}_{purpose}"
                self.unresolved.pop(code, None)
        returned = sorted(code for code in requested if code in self._snapshots)
        missing = sorted(requested_set - set(returned))
        duplicates = sorted(code for code, count in Counter(parsed_codes).items() if count > 1)
        raw_codes = structural["raw_security_code_array"]
        audit = {
            "capability": "HISTORICAL_SNAPSHOT",
            "transport": transport,
            "market": market,
            "purpose": purpose,
            "requested_codes": requested,
            "requested_code_count": len(requested),
            "raw_response_code_count": len(set(raw_codes)),
            "raw_security_code_array": raw_codes,
            "raw_field_array_lengths": structural["raw_field_array_lengths"],
            "raw_table_count": structural["raw_table_count"],
            "response_shape": structural["response_shape"],
            "parser_branch_used": structural["parser_branch_used"],
            "parser_output_code_count": len(set(parsed_codes)),
            "normalized_output_code_count": len(returned),
            "missing_codes": missing,
            "duplicate_codes": duplicates,
            "unexpected_codes": unexpected,
            "null_only_codes": sorted(null_only),
            "malformed_rows": malformed,
            "returned_rows": len(rows),
            "latency_ms": round((time.perf_counter() - begun) * 1000),
            "error_category": error_category,
            "concurrency": 1,
        }
        self.batch_audits.append(audit)
        return audit

    def _minute_fallback(self, code: str, market: str) -> None:
        if self.external_calls >= self.maximum_calls:
            self.unresolved[code] = "UNRESOLVED_PROVIDER_DATA"
            return
        self._reserve_call([code])
        self.minute_fallback_count += 1
        self.hf_batches += 1
        begun = time.perf_counter()
        start = f"{self._trade_date.isoformat()} 09:30:00"
        try:
            values = self.provider.get_minute_bars_batch([code], start, self._asof, "1m")
            bars = values.get(code, [])
            helper = AsOfMarketDataResolver(
                None,
                self.provider,
                {
                    "volume_semantics": "INCREMENTAL_SHARES",
                    "amount_semantics": "INCREMENTAL_CNY",
                    "near_cutoff_tolerance_seconds": 180,
                },
            )
            snapshot, normalized_bars = helper.reconstruct(
                code,
                bars,
                self._trade_date,
                self._cutoff,
                self._previous.get(code),
                self.now(),
                self.now(),
            )
            item = snapshot.to_dict()
            if item.get("data_quality") in {"VALID_EXACT", "VALID_NEAR_CUTOFF"}:
                item["data_quality"] = "VALID_RECONSTRUCTED"
                self._snapshots[code] = item
                self._minute_series[code] = normalized_bars
                self.sources[code] = "IFIND_HIGH_FREQUENCY_1M"
                self.unresolved.pop(code, None)
            elif not bars:
                self.unresolved[code] = "NO_TRADE_CONFIRMED"
            self.audit.append(self._audit("SINGLE_MINUTE_FALLBACK", 1, [code], len(bars), begun, None))
        except Exception as exc:
            self.unresolved[code] = "UNRESOLVED_PROVIDER_DATA"
            self.audit.append(self._audit("SINGLE_MINUTE_FALLBACK", 1, [code], 0, begun, type(exc).__name__))

    def _minute_batch_fallback(self, codes: list[str], market: str) -> None:
        pending = [code for code in codes if code not in self._snapshots]
        size = max(1, int(self.config["minute_reconstruction_batch_size"]))
        start = f"{self._trade_date.isoformat()} 09:30:00"
        for batch_id, offset in enumerate(range(0, len(pending), size), 1):
            batch = pending[offset : offset + size]
            if self.external_calls >= self.maximum_calls:
                for code in batch:
                    self.unresolved[code] = "UNRESOLVED_PROVIDER_DATA"
                break
            begun = time.perf_counter()
            self._reserve_call(batch)
            self.minute_fallback_count += 1
            self.hf_batches += 1
            try:
                values = self.provider.get_minute_bars_batch(batch, start, self._asof, "1m")
                helper = AsOfMarketDataResolver(
                    None,
                    self.provider,
                    {
                        "volume_semantics": "INCREMENTAL_SHARES",
                        "amount_semantics": "INCREMENTAL_CNY",
                        "near_cutoff_tolerance_seconds": 180,
                    },
                )
                returned_rows = 0
                for code in batch:
                    bars = values.get(code, [])
                    returned_rows += len(bars)
                    snapshot, normalized_bars = helper.reconstruct(
                        code,
                        bars,
                        self._trade_date,
                        self._cutoff,
                        self._previous.get(code),
                        self.now(),
                        self.now(),
                    )
                    item = snapshot.to_dict()
                    if item.get("data_quality") in {"VALID_EXACT", "VALID_NEAR_CUTOFF"}:
                        item["data_quality"] = "VALID_RECONSTRUCTED"
                        self._snapshots[code] = item
                        self._minute_series[code] = normalized_bars
                        self.sources[code] = "IFIND_HIGH_FREQUENCY_1M_BATCH"
                        self.unresolved.pop(code, None)
                    elif not bars:
                        self.unresolved[code] = "NO_TRADE_CONFIRMED"
                    else:
                        self.unresolved[code] = "DATA_CONFLICT"
                self.post_cutoff_rows_excluded += helper.excluded_post_cutoff_rows
                self.audit.append(self._audit("MISSING_MINUTE_BATCH_FALLBACK", batch_id, batch, returned_rows, begun, None))
            except Exception as exc:
                for code in batch:
                    self.unresolved[code] = "UNRESOLVED_PROVIDER_DATA"
                self.audit.append(self._audit("MISSING_MINUTE_BATCH_FALLBACK", batch_id, batch, 0, begun, type(exc).__name__))

    def _snapshot_from_row(self, code: str, row: dict[str, Any]) -> dict[str, Any] | None:
        stamp = str(row.get("time") or row.get("tradeTime") or "")
        if not stamp or self._after_cutoff(stamp):
            if stamp:
                self.post_cutoff_rows_excluded += 1
            return None
        wrapper = type("Snapshot", (), {
            "stock_code": code,
            "datetime": stamp,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "latest": row.get("latest"),
            "pre_close": row.get("preClose") or self._previous.get(code),
            "volume": row.get("volume"),
            "amount": row.get("amount"),
        })()
        try:
            result = self._snapshot_dict(
                code, wrapper, self._previous.get(code), self._trade_date, self._cutoff
            )
        except (TypeError, ValueError):
            return None
        result["suspension_status"] = "TRADING_OR_QUOTED"
        return result

    def _valid_snapshot_row(self, row: dict[str, Any]) -> bool:
        return all(row.get(field) is not None for field in VALID_SNAPSHOT_FIELDS) and bool(
            row.get("time") or row.get("tradeTime")
        )

    @staticmethod
    def _null_only(row: dict[str, Any]) -> bool:
        ignored = {"thscode", "code", "ts_code", "time", "tradeTime", "tradeDate"}
        values = [value for key, value in row.items() if key not in ignored]
        return bool(values) and all(value in (None, "") for value in values)

    def _reserve_call(self, codes: list[str]) -> None:
        if self.external_calls >= self.maximum_calls:
            raise RuntimeError("FULL_A_RUN_CALL_BUDGET_EXHAUSTED")
        self.external_calls += 1
        for code in codes:
            self.attempts[normalize_ts_code(code)] += 1

    @property
    def _asof(self) -> str:
        return f"{self._trade_date.isoformat()} {self._cutoff.isoformat()}"

    def _after_cutoff(self, value: str) -> bool:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=SHANGHAI)
            cutoff = datetime.combine(self._trade_date, self._cutoff, tzinfo=SHANGHAI)
            return parsed.astimezone(SHANGHAI) > cutoff
        except ValueError:
            return True

    @staticmethod
    def _empty_audit(codes: list[str], market: str, purpose: str) -> dict[str, Any]:
        return {
            "market": market,
            "purpose": purpose,
            "requested_codes": list(codes),
            "missing_codes": [],
            "unexpected_codes": [],
            "normalized_output_code_count": 0,
            "parser_branch_used": ["CACHE_REUSE_NO_REQUEST"],
        }
