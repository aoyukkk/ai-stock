from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from datasource.tushare_provider import (
    DEFAULT_TOKEN_ENV,
    TushareEndpointResult,
    TushareMarketDataProvider,
    _env_file_value,
)
from market_forecast.data_validation import INDEX_FIELDS, DataQualityResult, MarketDataValidationError, validate_market_data


SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class LoadedMarketData:
    quality: DataQualityResult
    expected_latest_trade_date: pd.Timestamp
    cache_paths: dict[str, str]
    provider_diagnostics: dict[str, Any]


class MarketForecastDataLoader:
    def __init__(
        self,
        provider: TushareMarketDataProvider | Any | None = None,
        *,
        cache_root: str | Path = "data/cache/market_forecast",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider
        self.cache_root = Path(cache_root)
        self.now = now or (lambda: datetime.now(SHANGHAI))

    def load(
        self,
        *,
        index_code: str,
        start_date: str,
        end_date: str,
        exchange: str = "SSE",
        market_close_time: str = "15:30",
        refresh_data: bool = False,
    ) -> LoadedMarketData:
        if index_code != "000001.SH" or exchange != "SSE":
            raise MarketDataValidationError("FROZEN_SCOPE_REQUIRES_000001_SH_AND_SSE")
        self._load_local_token()
        provider = self.provider or TushareMarketDataProvider(cache_enabled=True)
        self.provider = provider
        start = _parse_date(start_date)
        requested_end = self._resolve_requested_end(end_date, market_close_time)

        calendar_path = self.cache_root / "trade_cal" / f"{exchange}.parquet"
        index_path = self.cache_root / "index_daily" / f"{index_code}.parquet"
        calendar = self._load_calendar(provider, calendar_path, start, requested_end, exchange, refresh_data)
        open_dates = pd.to_datetime(
            calendar.loc[pd.to_numeric(calendar["is_open"], errors="coerce") == 1, "cal_date"],
            format="mixed",
            errors="coerce",
        ).dropna()
        eligible = open_dates[(open_dates.dt.date >= start) & (open_dates.dt.date <= requested_end)]
        if eligible.empty:
            raise MarketDataValidationError("NO_OPEN_TRADE_DATE_IN_REQUESTED_RANGE")
        expected_latest = pd.Timestamp(eligible.max()).normalize()
        index_frame = self._load_index(
            provider,
            index_path,
            index_code,
            start,
            expected_latest.date(),
            refresh_data,
            open_trade_dates={value.date() for value in eligible},
        )
        selected = index_frame[
            (pd.to_datetime(index_frame["trade_date"], format="mixed", errors="coerce").dt.date >= start)
            & (pd.to_datetime(index_frame["trade_date"], format="mixed", errors="coerce").dt.date <= expected_latest.date())
        ].copy()
        calendar_selected = calendar[
            (pd.to_datetime(calendar["cal_date"], format="mixed", errors="coerce").dt.date >= start)
            & (pd.to_datetime(calendar["cal_date"], format="mixed", errors="coerce").dt.date <= expected_latest.date())
        ].copy()
        quality = validate_market_data(
            selected,
            calendar_selected,
            index_code=index_code,
            expected_latest_trade_date=expected_latest,
        )
        diagnostics = provider.diagnostics() if hasattr(provider, "diagnostics") else {}
        diagnostics = {key: value for key, value in diagnostics.items() if "token" not in key.lower()}
        return LoadedMarketData(
            quality=quality,
            expected_latest_trade_date=expected_latest,
            cache_paths={"index_daily": str(index_path.resolve()), "trade_cal": str(calendar_path.resolve())},
            provider_diagnostics=diagnostics,
        )

    def _load_calendar(
        self,
        provider: Any,
        path: Path,
        start: date,
        end: date,
        exchange: str,
        refresh_data: bool,
    ) -> pd.DataFrame:
        cached = _read_parquet(path, columns=("exchange", "cal_date", "is_open", "pretrade_date"))
        coverage_path = path.with_suffix(".coverage.json")
        coverage = _read_coverage(coverage_path)
        ranges = _coverage_missing_ranges(coverage, start, end) if coverage else _missing_ranges(cached, "cal_date", start, end)
        fetched: list[pd.DataFrame] = []
        for range_start, range_end in ranges:
            result = provider.query_endpoint(
                "trade_cal",
                params={
                    "exchange": exchange,
                    "start_date": range_start.strftime("%Y%m%d"),
                    "end_date": range_end.strftime("%Y%m%d"),
                    "is_open": "1",
                },
                fields="exchange,cal_date,is_open,pretrade_date",
                required_fields={"cal_date", "is_open"},
                use_cache=not refresh_data,
                write_cache=True,
            )
            _raise_for_result(result, "trade_cal", allow_empty=True)
            if result.records:
                fetched.append(pd.DataFrame(result.records))
        merged = _merge_cached_frames(cached, fetched, "cal_date")
        if merged.empty:
            raise MarketDataValidationError("TRADE_CAL_CACHE_AND_PROVIDER_EMPTY")
        _write_parquet_atomic(merged, path)
        checked_start = min(start, _parse_date(coverage["checked_start"])) if coverage else start
        checked_end = max(end, _parse_date(coverage["checked_end"])) if coverage else end
        coverage_path.write_text(
            json.dumps(
                {"checked_start": checked_start.isoformat(), "checked_end": checked_end.isoformat()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return merged

    def _load_index(
        self,
        provider: Any,
        path: Path,
        index_code: str,
        start: date,
        end: date,
        refresh_data: bool,
        open_trade_dates: set[date],
    ) -> pd.DataFrame:
        cached = _read_parquet(path, columns=INDEX_FIELDS)
        ranges = _missing_ranges(cached, "trade_date", start, end)
        fetched: list[pd.DataFrame] = []
        for range_start, range_end in ranges:
            if not any(range_start <= trade_day <= range_end for trade_day in open_trade_dates):
                continue
            result = provider.query_endpoint(
                "index_daily",
                params={
                    "ts_code": index_code,
                    "start_date": range_start.strftime("%Y%m%d"),
                    "end_date": range_end.strftime("%Y%m%d"),
                },
                fields=",".join(INDEX_FIELDS),
                required_fields=set(INDEX_FIELDS),
                use_cache=not refresh_data,
                write_cache=True,
            )
            _raise_for_result(result, "index_daily", allow_empty=False)
            fetched.append(pd.DataFrame(result.records))
        merged = _merge_cached_frames(cached, fetched, "trade_date")
        if merged.empty:
            raise MarketDataValidationError("INDEX_DAILY_CACHE_AND_PROVIDER_EMPTY")
        _write_parquet_atomic(merged.loc[:, INDEX_FIELDS], path)
        return merged

    def _resolve_requested_end(self, value: str, market_close_time: str) -> date:
        if value != "auto":
            return _parse_date(value)
        local = self.now().astimezone(SHANGHAI)
        hour, minute = (int(part) for part in market_close_time.split(":", 1))
        cutoff = time(hour, minute)
        return local.date() if local.time() >= cutoff else local.date() - timedelta(days=1)

    @staticmethod
    def _load_local_token() -> None:
        if os.getenv(DEFAULT_TOKEN_ENV, "").strip():
            return
        token = _env_file_value(Path(".env"), DEFAULT_TOKEN_ENV)
        if token:
            os.environ[DEFAULT_TOKEN_ENV] = token


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    text = str(value).split("T", 1)[0].replace("-", "")
    return datetime.strptime(text, "%Y%m%d").date()


def _read_parquet(path: Path, *, columns: tuple[str, ...]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=list(columns))
    frame = pd.read_parquet(path)
    missing = set(columns) - set(frame.columns)
    if missing:
        raise MarketDataValidationError(f"CACHE_FIELDS_MISSING:{path}:{','.join(sorted(missing))}")
    return frame.loc[:, list(columns)].copy()


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _missing_ranges(frame: pd.DataFrame, date_column: str, start: date, end: date) -> list[tuple[date, date]]:
    if start > end:
        return []
    if frame.empty:
        return [(start, end)]
    values = pd.to_datetime(frame[date_column], format="mixed", errors="coerce").dropna()
    if values.empty:
        raise MarketDataValidationError(f"CACHE_DATE_COLUMN_INVALID:{date_column}")
    ranges: list[tuple[date, date]] = []
    minimum = values.min().date()
    maximum = values.max().date()
    if start < minimum:
        ranges.append((start, min(end, minimum - timedelta(days=1))))
    if end > maximum:
        ranges.append((max(start, maximum + timedelta(days=1)), end))
    return [item for item in ranges if item[0] <= item[1]]


def _read_coverage(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        _parse_date(payload["checked_start"])
        _parse_date(payload["checked_end"])
        return {"checked_start": str(payload["checked_start"]), "checked_end": str(payload["checked_end"])}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _coverage_missing_ranges(coverage: dict[str, str], start: date, end: date) -> list[tuple[date, date]]:
    checked_start = _parse_date(coverage["checked_start"])
    checked_end = _parse_date(coverage["checked_end"])
    ranges: list[tuple[date, date]] = []
    if start < checked_start:
        ranges.append((start, min(end, checked_start - timedelta(days=1))))
    if end > checked_end:
        ranges.append((max(start, checked_end + timedelta(days=1)), end))
    return [item for item in ranges if item[0] <= item[1]]


def _merge_cached_frames(cached: pd.DataFrame, fetched: list[pd.DataFrame], date_column: str) -> pd.DataFrame:
    frames = [frame for frame in [cached, *fetched] if not frame.empty]
    if not frames:
        return cached.copy()
    merged = pd.concat(frames, ignore_index=True)
    merged[date_column] = pd.to_datetime(merged[date_column], format="mixed", errors="coerce").dt.strftime("%Y%m%d")
    if merged[date_column].isna().any():
        raise MarketDataValidationError(f"INVALID_DATE_IN_CACHE_MERGE:{date_column}")
    duplicates = merged[merged.duplicated(date_column, keep=False)]
    if not duplicates.empty:
        comparable = [column for column in merged.columns if column != date_column]
        for _, group in duplicates.groupby(date_column, sort=False):
            normalized = group[comparable].astype(str).replace({"nan": "", "None": ""}).drop_duplicates()
            if len(normalized) > 1:
                raise MarketDataValidationError(f"CONFLICTING_DUPLICATE_DATE:{group[date_column].iloc[0]}")
        merged = merged.drop_duplicates(date_column, keep="last")
    return merged.sort_values(date_column, kind="stable").reset_index(drop=True)


def _raise_for_result(result: TushareEndpointResult | Any, api_name: str, *, allow_empty: bool) -> None:
    status = str(getattr(result, "status", "error"))
    if status == "available" or (allow_empty and status == "empty"):
        return
    error_type = getattr(result, "error_type", None) or status
    message = getattr(result, "error_message", None) or status
    raise MarketDataValidationError(f"{api_name.upper()}_FETCH_FAILED:{error_type}:{message}")
