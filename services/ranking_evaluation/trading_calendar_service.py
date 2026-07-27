from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from services.ranking_evaluation.constants import ROOT_DIR, load_config
from services.ranking_evaluation.utils import stable_hash


class RankingTradingCalendarService:
    """Explicit SSE trade calendar backed by cached Tushare trade_cal rows."""

    def __init__(
        self,
        *,
        open_dates: list[date] | None = None,
        cache_root: Path | None = None,
    ) -> None:
        self.config = load_config()
        self.cache_root = cache_root or (
            ROOT_DIR / str(self.config["trade_calendar_cache_root"])
        )
        self._injected = sorted(set(open_dates or []))
        self._calendar: dict[date, bool] | None = None

    def is_open(self, day: date) -> bool:
        if self._injected:
            return day in self._injected
        return self._load().get(day, False)

    def open_dates(self, start: date, end: date) -> list[date]:
        source = self._injected or sorted(
            day for day, is_open in self._load().items() if is_open
        )
        return [day for day in source if start <= day <= end]

    def horizon_dates(self, ranking_date: date, horizons: tuple[int, ...]) -> dict[int, date]:
        if not self.is_open(ranking_date):
            raise ValueError("RANKING_TRADE_DATE_NOT_OPEN")
        candidates = [
            item for item in (self._injected or sorted(self._load()))
            if item > ranking_date and self.is_open(item)
        ]
        maximum = max(horizons)
        if len(candidates) < maximum:
            raise ValueError("TRADE_CALENDAR_HORIZON_INCOMPLETE")
        return {horizon: candidates[horizon - 1] for horizon in horizons}

    def latest_open_on_or_before(self, day: date) -> date | None:
        values = [
            item for item in (self._injected or sorted(self._load()))
            if item <= day and self.is_open(item)
        ]
        return values[-1] if values else None

    def version_hash(self) -> str:
        values = [
            (item.isoformat(), self.is_open(item))
            for item in (self._injected or sorted(self._load()))
        ]
        return stable_hash(values)

    def _load(self) -> dict[date, bool]:
        if self._calendar is not None:
            return self._calendar
        values: dict[date, bool] = {}
        conflicts: set[date] = set()
        for path in sorted(self.cache_root.glob("trade_cal_*.json")):
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(rows, list):
                continue
            for row in rows:
                if str(row.get("exchange") or "SSE").upper() not in {"", "SSE"}:
                    continue
                try:
                    day = datetime.strptime(str(row["cal_date"]), "%Y%m%d").date()
                except (KeyError, TypeError, ValueError):
                    continue
                is_open = str(row.get("is_open", "0")) in {"1", "True", "true"}
                if day in values and values[day] != is_open:
                    conflicts.add(day)
                values[day] = is_open
        if conflicts:
            raise ValueError(
                "TRADE_CALENDAR_CONFLICT:" + ",".join(sorted(x.isoformat() for x in conflicts))
            )
        if not values:
            raise ValueError("TRADE_CALENDAR_CACHE_UNAVAILABLE")
        self._calendar = values
        return values
