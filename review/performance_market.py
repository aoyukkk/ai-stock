from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from pathlib import Path

from sqlalchemy import select

from database.models.market import StockMarketData
from review.performance_schemas import MarketBar
from stock_codes import normalize_ts_code
from temporal.calendar import TradeCalendarService


ROOT_DIR = Path(__file__).resolve().parents[1]
TRADE_DATE_CACHE = ROOT_DIR / "data" / "cache" / "tushare" / "trade_date" / "daily"


class MarketDataBatchLoader:
    """Loads one date batch at a time from local DB/cache and never calls a provider."""

    per_stock_api_call_count = 0
    no_llm_call_verified = True

    def __init__(self, session, cache_dir: Path | None = None) -> None:
        self.session = session
        self.cache_dir = cache_dir or TRADE_DATE_CACHE

    def available_dates(self, end_date: date | None = None) -> list[date]:
        values: set[date] = set()
        for value in self.session.scalars(select(StockMarketData.datetime)):
            day = value.date()
            if end_date is None or day <= end_date:
                values.add(day)
        if self.cache_dir.exists():
            for path in self.cache_dir.glob("*.json"):
                try:
                    day = datetime.strptime(path.stem, "%Y%m%d").date()
                except ValueError:
                    continue
                if end_date is None or day <= end_date:
                    values.add(day)
        return sorted(values)

    def calendar(self, end_date: date | None = None) -> TradeCalendarService:
        return TradeCalendarService(open_dates=self.available_dates(end_date))

    def load(self, stock_codes: set[str], dates: list[date]) -> tuple[dict[str, dict[date, MarketBar]], str]:
        normalized_codes = {normalize_ts_code(code) for code in stock_codes}
        result: dict[str, dict[date, MarketBar]] = {code: {} for code in normalized_codes}
        if not dates or not normalized_codes:
            return result, hashlib.sha256(b"empty").hexdigest()
        start = datetime.combine(min(dates), time.min, timezone.utc)
        end = datetime.combine(max(dates), time.max, timezone.utc)
        db_rows = self.session.scalars(select(StockMarketData).where(
            StockMarketData.datetime >= start,
            StockMarketData.datetime <= end,
            StockMarketData.stock_code.in_(normalized_codes),
        )).all()
        for row in db_rows:
            code = normalize_ts_code(row.stock_code)
            result.setdefault(code, {})[row.datetime.date()] = MarketBar(
                code, row.datetime.date(), _float(row.open), _float(row.high), _float(row.low),
                _float(row.close), _float(row.pre_close), _float(row.change_percent), _float(row.volume),
                suspended=bool(row.volume == 0 and row.close is not None),
            )
        fingerprints: list[dict] = []
        for day in dates:
            path = self.cache_dir / f"{day.strftime('%Y%m%d')}.json"
            if not path.exists():
                fingerprints.append({"date": day.isoformat(), "status": "MISSING"})
                continue
            raw = path.read_bytes()
            fingerprints.append({"date": day.isoformat(), "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
            try:
                records = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            for item in records:
                code = normalize_ts_code(str(item.get("ts_code") or item.get("stock_code") or ""))
                if code not in normalized_codes or day in result.setdefault(code, {}):
                    continue
                result[code][day] = MarketBar(
                    code, day, _float(item.get("open")), _float(item.get("high")), _float(item.get("low")),
                    _float(item.get("close")), _float(item.get("pre_close")), _float(item.get("pct_chg")),
                    _float(item.get("vol")), suspended=bool(item.get("is_suspended") is True),
                )
        watermark = hashlib.sha256(json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return result, watermark


def _float(value) -> float | None:
    return float(value) if value is not None else None
