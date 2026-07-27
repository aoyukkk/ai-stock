from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from database.models.stock import StockMaster
from services.ranking_evaluation.constants import ROOT_DIR, load_config
from services.ranking_evaluation.schemas import DailyPrice
from services.ranking_evaluation.utils import stable_hash
from stock_codes import normalize_ts_code
from stock_codes import display_stock_code


@dataclass(frozen=True)
class DailyBatch:
    trade_date: date
    rows: dict[str, DailyPrice]
    duplicate_codes: frozenset[str]
    invalid_date_rows: tuple[str, ...]
    source_hash: str
    status: str


class RankingMarketDataService:
    """Strictly local, trade-date batch market data. It never calls a provider."""

    external_api_calls = 0
    llm_calls = 0

    def __init__(
        self,
        session,
        *,
        daily_root: Path | None = None,
        adj_root: Path | None = None,
        injected_batches: dict[date, list[dict[str, Any]]] | None = None,
        injected_adjustments: dict[date, dict[str, float]] | None = None,
    ) -> None:
        config = load_config()
        self.session = session
        self.daily_root = daily_root or (ROOT_DIR / str(config["daily_cache_root"]))
        self.adj_root = adj_root or (ROOT_DIR / str(config["adj_factor_cache_root"]))
        self.injected_batches = injected_batches or {}
        self.injected_adjustments = injected_adjustments or {}

    def load_day(self, trade_date: date) -> DailyBatch:
        if trade_date in self.injected_batches:
            rows = self.injected_batches[trade_date]
            source_hash = stable_hash(rows)
            source = "TEST_INJECTED_DAILY"
        else:
            path = self.daily_root / f"{trade_date.strftime('%Y%m%d')}.json"
            if not path.exists():
                return DailyBatch(
                    trade_date, {}, frozenset(), (), stable_hash(["MISSING", trade_date]), "MISSING"
                )
            raw = path.read_bytes()
            try:
                rows = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return DailyBatch(
                    trade_date, {}, frozenset(), (), stable_hash(raw.hex()), "PIPELINE_ERROR"
                )
            source_hash = stable_hash({"file": path.name, "sha256": stable_hash(raw.hex())})
            source = f"TUSHARE_DAILY_CACHE:{path.name}"
        if not isinstance(rows, list):
            return DailyBatch(
                trade_date, {}, frozenset(), (), source_hash, "PIPELINE_ERROR"
            )
        result: dict[str, DailyPrice] = {}
        duplicates: set[str] = set()
        invalid_dates: list[str] = []
        for row in rows:
            try:
                code = normalize_ts_code(
                    str(row.get("ts_code") or row.get("stock_code") or "")
                )
            except ValueError:
                invalid_dates.append(
                    f"INVALID_CODE:{row.get('ts_code') or row.get('stock_code') or ''}"
                )
                continue
            row_date = _row_trade_date(row) or trade_date
            if row_date != trade_date:
                invalid_dates.append(code)
                continue
            if code in result:
                duplicates.add(code)
                continue
            result[code] = DailyPrice(
                stock_code=code,
                trade_date=row_date,
                close=_float(row.get("close")),
                volume=_float(row.get("vol") if "vol" in row else row.get("volume")),
                source=source,
                source_hash=stable_hash(row),
                suspended=bool(row.get("is_suspended")) or _float(row.get("vol")) == 0,
            )
        status = "ABNORMAL" if duplicates or invalid_dates else "NORMAL"
        return DailyBatch(
            trade_date,
            result,
            frozenset(duplicates),
            tuple(sorted(invalid_dates)),
            source_hash,
            status,
        )

    def adjustment_factor(self, trade_date: date, stock_code: str) -> tuple[float | None, str]:
        code = normalize_ts_code(stock_code)
        if trade_date in self.injected_adjustments:
            value = self.injected_adjustments[trade_date].get(code)
            return value, stable_hash([trade_date, code, value])
        path = self.adj_root / f"{trade_date.strftime('%Y%m%d')}.json"
        if not path.exists():
            return None, stable_hash(["ADJ_MISSING", trade_date, code])
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None, stable_hash(["ADJ_ERROR", trade_date, code])
        matches = [
            row for row in rows
            if normalize_ts_code(str(row.get("ts_code") or row.get("stock_code") or "")) == code
        ]
        if len(matches) != 1:
            return None, stable_hash(["ADJ_COUNT", trade_date, code, len(matches)])
        value = _float(matches[0].get("adj_factor"))
        return value, stable_hash(matches[0])

    def stock_status(self, stock_code: str) -> str:
        code = normalize_ts_code(stock_code)
        row = self.session.scalar(
            select(StockMaster).where(
                StockMaster.code.in_((code, display_stock_code(code)))
            )
        )
        return str(row.status or "UNKNOWN").upper() if row else "UNKNOWN"


def _row_trade_date(row: dict[str, Any]) -> date | None:
    raw = row.get("trade_date")
    if raw in (None, ""):
        return None
    text = str(raw)
    for pattern in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return date.min


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
