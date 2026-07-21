from __future__ import annotations

import time
from datetime import date
from typing import Any, Callable, TypeVar

from datasource.ifind.http.client import IFindHttpClient
from datasource.ifind.http.models import HistoricalSnapshot, IndexDailyBar, IndexRealtimeQuote, MinuteBar, RealtimeQuote
from datasource.ifind.http.normalizer import normalize_probe_response, normalize_rows
from stock_codes import normalize_ts_code


T = TypeVar("T")


class IFindHttpP0Provider:
    """Verified read-only HTTP capabilities. Production routing remains disabled by config."""

    def __init__(
        self,
        client: IFindHttpClient,
        *,
        enabled: bool = False,
        cache_ttl_seconds: int = 30,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.enabled = enabled
        self.cache_ttl_seconds = max(0, cache_ttl_seconds)
        self.monotonic = monotonic
        self._cache: dict[tuple[Any, ...], tuple[float, list[Any]]] = {}

    def index_daily(self, codes: list[str], start_date: date, end_date: date) -> list[IndexDailyBar]:
        normalized_codes = _codes(codes)
        return self._cached(("index_daily", tuple(normalized_codes), start_date, end_date), lambda: self._index_daily(normalized_codes, start_date, end_date))

    get_index_daily = index_daily

    def index_realtime(self, codes: list[str]) -> list[IndexRealtimeQuote]:
        normalized_codes = _codes(codes)
        return self._cached(("index_realtime", tuple(normalized_codes)), lambda: self._index_realtime(normalized_codes))

    get_index_realtime = index_realtime

    def get_index_latest_completed(self, codes: list[str], trade_date: date) -> list[IndexDailyBar]:
        return self.get_index_daily(codes, trade_date, trade_date)

    def stock_realtime(self, codes: list[str]) -> list[RealtimeQuote]:
        normalized_codes = _codes(codes)
        return self._cached(("stock_realtime", tuple(normalized_codes)), lambda: self._stock_realtime(normalized_codes))

    get_realtime = stock_realtime

    def historical_snapshots(self, codes: list[str], asof_time: str) -> list[HistoricalSnapshot]:
        """Return provider snapshots for an explicit historical exchange time."""
        normalized = _codes(codes)
        response = self._post("snap_shot", {
            "codes": ",".join(normalized),
            "indicators": "tradeDate,tradeTime,preClose,open,high,low,latest,volume,amount",
            "starttime": asof_time,
            "endtime": asof_time,
        })
        return [HistoricalSnapshot(
            stock_code=normalize_ts_code(row["thscode"]), datetime=str(row["time"]),
            open=_number(row.get("open")), high=_number(row.get("high")), low=_number(row.get("low")),
            latest=_number(row.get("latest")), pre_close=_number(row.get("preClose")),
            volume=_number(row.get("volume")), amount=_number(row.get("amount")),
            provider_time=str(row["time"]), data_status=response["status"],
        ) for row in response["rows"]]

    get_historical_snapshots = historical_snapshots

    def get_realtime_with_status(self, codes: list[str]) -> dict[str, Any]:
        rows = self.stock_realtime(codes)
        return {"status": rows[0].data_status if rows else "NO_DATA", "items": rows}

    def minute_bars(self, code: str, start_time: str, end_time: str) -> list[MinuteBar]:
        normalized_code = normalize_ts_code(code)
        return self._cached(("minute", normalized_code, start_time, end_time), lambda: self._minute(normalized_code, start_time, end_time))

    def get_minute_bars(self, code: str, start_time: str, end_time: str, interval: str = "1m") -> list[MinuteBar]:
        if interval not in {"1m", "1"}:
            raise ValueError("IFIND_MINUTE_INTERVAL_NOT_VERIFIED")
        return self.minute_bars(code, start_time, end_time)

    def get_minute_bars_batch(self, codes: list[str], start_time: str, end_time: str, interval: str = "1m") -> dict[str, list[MinuteBar]]:
        """Read a verified multi-code high-frequency response and split it by canonical code."""
        if interval not in {"1m", "1"}:
            raise ValueError("IFIND_MINUTE_INTERVAL_NOT_VERIFIED")
        normalized = _codes(codes)
        response = self._post("high_frequency", {
            "codes": ",".join(normalized), "indicators": "open,high,low,close,volume,amount",
            "starttime": start_time, "endtime": end_time, "functionpara": {"Interval": "1"},
        })
        output = {code: [] for code in normalized}
        for row in response["rows"]:
            code = normalize_ts_code(row["thscode"])
            if code in output:
                output[code].append(MinuteBar(
                    stock_code=code, datetime=str(row["time"]), open=_number(row["open"]),
                    high=_number(row["high"]), low=_number(row["low"]), close=_number(row["close"]),
                    volume=_number(row["volume"]), amount=_number(row["amount"]), data_status=response["status"],
                ))
        return output

    def clear_cache(self) -> None:
        self._cache.clear()

    def _index_daily(self, codes: list[str], start_date: date, end_date: date) -> list[IndexDailyBar]:
        response = self._post("cmd_history_quotation", {
            "codes": ",".join(codes), "indicators": "open,high,low,close,volume,amount",
            "startdate": start_date.isoformat(), "enddate": end_date.isoformat(), "functionpara": {"Interval": "D"},
        })
        return [IndexDailyBar(
            index_code=row["thscode"], datetime=str(row["time"]), open=_number(row["open"]), high=_number(row["high"]),
            low=_number(row["low"]), close=_number(row["close"]), volume=_number(row["volume"]), amount=_number(row["amount"]),
            data_status=response["status"],
        ) for row in response["rows"]]

    def _index_realtime(self, codes: list[str]) -> list[IndexRealtimeQuote]:
        response = self._realtime(codes)
        return [IndexRealtimeQuote(
            index_code=row["thscode"], datetime=str(row["time"]), latest=_number(row["latest"]),
            open=_number(row["open"]), high=_number(row["high"]), low=_number(row["low"]),
            volume=_number(row["volume"]), amount=_number(row["amount"]), data_status=response["status"],
        ) for row in response["rows"]]

    def _stock_realtime(self, codes: list[str]) -> list[RealtimeQuote]:
        response = self._realtime(codes)
        return [RealtimeQuote(
            stock_code=row["thscode"], datetime=str(row["time"]), latest=_number(row["latest"]),
            open=_number(row["open"]), high=_number(row["high"]), low=_number(row["low"]),
            volume=_number(row["volume"]), amount=_number(row["amount"]), data_status=response["status"],
        ) for row in response["rows"]]

    def _minute(self, code: str, start_time: str, end_time: str) -> list[MinuteBar]:
        response = self._post("high_frequency", {
            "codes": code, "indicators": "open,high,low,close,volume,amount",
            "starttime": start_time, "endtime": end_time, "functionpara": {"Interval": "1"},
        })
        return [MinuteBar(
            stock_code=row["thscode"], datetime=str(row["time"]), open=_number(row["open"]), high=_number(row["high"]),
            low=_number(row["low"]), close=_number(row["close"]), volume=_number(row["volume"]), amount=_number(row["amount"]),
            data_status=response["status"],
        ) for row in response["rows"]]

    def _realtime(self, codes: list[str]) -> dict[str, Any]:
        return self._post("real_time_quotation", {
            "codes": ",".join(codes), "indicators": "open,high,low,latest,volume,amount", "functionpara": {},
        })

    def _post(self, endpoint_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        response = self.client.post(endpoint_name, payload)
        summary = normalize_probe_response(response.payload)
        return {"rows": normalize_rows(response.payload), "status": summary.data_status}

    def _cached(self, key: tuple[Any, ...], loader: Callable[[], list[T]]) -> list[T]:
        self._require_enabled()
        cached = self._cache.get(key)
        now = self.monotonic()
        if cached and cached[0] >= now:
            return list(cached[1])
        values = loader()
        self._cache[key] = (now + self.cache_ttl_seconds, list(values))
        return values

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise RuntimeError("IFIND_HTTP_PROVIDER_DISABLED")


def _codes(codes: list[str]) -> list[str]:
    normalized = [normalize_ts_code(code) for code in codes]
    if not normalized:
        raise ValueError("IFIND_HTTP_CODES_REQUIRED")
    return normalized


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)
