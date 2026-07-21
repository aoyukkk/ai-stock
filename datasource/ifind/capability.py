from __future__ import annotations

import json
from datetime import date, timedelta
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.core.runtime_paths import tushare_cache_root
from database.models.stock import StockMaster
from datasource.ifind.schemas import (
    CapabilityResult,
    CapabilityStatus,
    IFindErrorCategory,
    ProductionRecommendation,
    SampleSelection,
)
from stock_codes import normalize_ts_code


CAPABILITY_CATALOG: tuple[tuple[str, str, ProductionRecommendation], ...] = (
    ("IDENTITY", "SDK import", ProductionRecommendation.P2_OPTIONAL),
    ("IDENTITY", "Login and logout", ProductionRecommendation.P2_OPTIONAL),
    ("IDENTITY", "Trial status and quota", ProductionRecommendation.NEED_FORMAL_ACCOUNT),
    ("STOCK_BASIC", "Stock master and code mapping", ProductionRecommendation.KEEP_TUSHARE_PRIMARY),
    ("DAILY_MARKET", "Daily OHLC and adjustment", ProductionRecommendation.KEEP_TUSHARE_PRIMARY),
    ("REALTIME", "Realtime batch snapshot", ProductionRecommendation.NEED_DATAFEED_PRODUCT),
    ("ORDER_BOOK", "Five-level order book", ProductionRecommendation.NEED_DATAFEED_PRODUCT),
    ("MINUTE", "Minute bars", ProductionRecommendation.NEED_DATAFEED_PRODUCT),
    ("AUCTION", "Opening auction", ProductionRecommendation.NEED_DATAFEED_PRODUCT),
    ("INDEX", "Index basic, daily, realtime and constituents", ProductionRecommendation.P0_INTEGRATE),
    ("INDUSTRY", "THS industry classification and market data", ProductionRecommendation.P0_INTEGRATE),
    ("CONCEPT", "Concept classification and market data", ProductionRecommendation.P1_INTEGRATE),
    ("COMPANY", "Company profile and main business", ProductionRecommendation.P1_INTEGRATE),
    ("FINANCIAL", "Financial statements and indicators", ProductionRecommendation.KEEP_TUSHARE_PRIMARY),
    ("CONSENSUS", "Consensus estimates and analyst ratings", ProductionRecommendation.P1_INTEGRATE),
    ("ANNOUNCEMENT", "Announcements and company events", ProductionRecommendation.P1_INTEGRATE),
    ("SPECIAL_MARKET", "Dragon-tiger, margin, research and capital flow", ProductionRecommendation.P1_INTEGRATE),
    ("MACRO", "Macro EDB", ProductionRecommendation.P2_OPTIONAL),
    ("OVERSEAS", "Overseas and cross-market data", ProductionRecommendation.P2_OPTIONAL),
    ("NEWS", "News metadata and timeliness", ProductionRecommendation.NEED_DATAFEED_PRODUCT),
)


class IFindSampleSelector:
    def __init__(self, session, config: dict[str, Any], cache_root: Path | None = None) -> None:
        self.session = session
        self.config = config
        self.cache_root = (cache_root or tushare_cache_root()).resolve()

    def select(self) -> list[SampleSelection]:
        selected = self._select_local_stocks()
        required = int(self.config.get("sample_stock_count", 4))
        if len(selected) < required:
            selected = self._fallback("stocks", required)
        indices = self._fallback("indices", int(self.config.get("sample_index_count", 2)))
        return selected[:required] + indices

    def _select_local_stocks(self) -> list[SampleSelection]:
        daily_dir = self.cache_root / "trade_date" / "daily"
        files = sorted(daily_dir.glob("*.json"), reverse=True) if daily_dir.exists() else []
        if not files:
            return []
        latest_path = files[0]
        try:
            rows = json.loads(latest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(rows, list):
            return []
        masters = {normalize_ts_code(item.code): item for item in self.session.scalars(select(StockMaster))}
        candidates: dict[str, list[tuple[float, str, StockMaster]]] = {"SH_MAIN": [], "SZ_MAIN": [], "CHINEXT": [], "STAR": []}
        latest_date = latest_path.stem
        decision_date = date.fromisoformat(f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:8]}")
        for row in rows:
            code = normalize_ts_code(str(row.get("ts_code") or row.get("code") or ""))
            master = masters.get(code)
            if not master or not _eligible(master, decision_date):
                continue
            board = _board(code)
            if board not in candidates:
                continue
            amount = _number(row.get("amount"))
            volume = _number(row.get("vol"))
            if amount <= 0 or volume <= 0:
                continue
            candidates[board].append((amount, code, master))
        result = []
        for board in ("SH_MAIN", "SZ_MAIN", "CHINEXT", "STAR"):
            if not candidates[board]:
                continue
            amount, code, _master = max(candidates[board], key=lambda item: (item[0], item[1]))
            result.append(SampleSelection(
                canonical_code=code, market=code.split(".")[-1], board=board,
                selection_reason="LOCAL_LATEST_HIGH_LIQUIDITY_NORMAL_LISTING",
                local_latest_trade_date=latest_date, source="LOCAL_DATABASE_AND_TUSHARE_CACHE",
            ))
        return result

    def _fallback(self, key: str, count: int) -> list[SampleSelection]:
        rows = list((self.config.get("fallback_samples") or {}).get(key) or [])
        return [SampleSelection(
            canonical_code=normalize_ts_code(str(row["canonical_code"])), market=str(row.get("market") or "UNKNOWN"),
            board=str(row.get("board") or "UNKNOWN"), selection_reason="SAMPLE_FALLBACK",
            local_latest_trade_date=None, source="CONFIG_FALLBACK",
        ) for row in rows[:count]]


def untested_capabilities(samples: list[SampleSelection], *, configured: bool, sdk_installed: bool, transport: str = "SDK") -> list[CapabilityResult]:
    codes = [item.canonical_code for item in samples]
    requires_sdk = transport == "SDK"
    error = IFindErrorCategory.SDK_NOT_INSTALLED if requires_sdk and not sdk_installed else None
    notes = ["SDK_NOT_INSTALLED: real capability calls were stopped"] if error else [f"{transport} dry run does not call the provider"]
    return [CapabilityResult(
        category=category, capability_name=name, status=CapabilityStatus.NOT_TESTED,
        authorized=None, configured=configured, sample_codes=codes,
        request_scope="DRY_RUN_OR_SDK_UNAVAILABLE", error_category=error,
        production_recommendation=recommendation, notes=notes,
    ) for category, name, recommendation in CAPABILITY_CATALOG]


def _eligible(master: StockMaster, decision_date: date) -> bool:
    name = (master.name or "").upper()
    status = (master.status or "L").upper()
    if "ST" in name or "退" in name or status not in {"L", "LISTED", "ACTIVE"}:
        return False
    return not master.list_date or master.list_date <= decision_date - timedelta(days=60)


def _board(code: str) -> str:
    plain = code.split(".")[0]
    if code.endswith(".SH") and plain.startswith("6") and not plain.startswith("688"):
        return "SH_MAIN"
    if code.endswith(".SZ") and plain.startswith(("000", "001", "002", "003")):
        return "SZ_MAIN"
    if code.endswith(".SZ") and plain.startswith("300"):
        return "CHINEXT"
    if code.endswith(".SH") and plain.startswith("688"):
        return "STAR"
    return "OTHER"


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def observed_delay_seconds(provider_time: datetime | None, observation_time: datetime) -> tuple[float | None, str]:
    if provider_time is None:
        return None, "TIME_UNKNOWN"
    if provider_time.tzinfo is None or observation_time.tzinfo is None:
        return None, "TIME_UNKNOWN"
    delay = max(0.0, (observation_time - provider_time).total_seconds())
    if delay <= 10:
        return delay, "REALTIME"
    if delay <= 300:
        return delay, "DELAYED"
    return delay, "STALE"
