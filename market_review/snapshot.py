from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml
from sqlalchemy import select

from backend.core.runtime_paths import tushare_cache_root
from database.models.stock import StockMaster
from market_review.schemas import DataStatus, IndexPerformance, MarketDailySnapshotData, SectorPerformance
from stock_codes import normalize_ts_code


class MarketDailySnapshotService:
    """Build an auditable market snapshot from local structured caches only."""

    def __init__(self, session, *, cache_root: Path | None = None, config: dict[str, Any] | None = None) -> None:
        self.session = session
        self.cache_root = (cache_root or tushare_cache_root()).resolve()
        self.config = config or load_market_review_config()

    def build(self, trade_date: date, decision_time: datetime) -> MarketDailySnapshotData:
        if decision_time.tzinfo is None:
            decision_time = decision_time.replace(tzinfo=timezone.utc)
        date_key = trade_date.strftime("%Y%m%d")
        daily_path = self._trade_date_path("daily", date_key)
        daily = self._load_rows(daily_path)
        if not daily:
            raise ValueError("MARKET_DAILY_CACHE_NOT_AVAILABLE")
        daily = [row for row in daily if _row_date(row) == date_key]
        if not daily:
            raise ValueError("MARKET_DAILY_CACHE_TRADE_DATE_MISMATCH")

        basic_path = self._trade_date_path("daily_basic", date_key)
        limit_path = self._trade_date_path("stk_limit", date_key)
        moneyflow_path = self._trade_date_path("moneyflow", date_key)
        daily_basic = self._load_rows(basic_path)
        limit_rows = self._load_rows(limit_path)
        moneyflow = self._load_rows(moneyflow_path)
        basic_by_code = {_code(row): row for row in daily_basic if _code(row)}
        limits_by_code = {_code(row): row for row in limit_rows if _code(row)}
        money_by_code = {_code(row): row for row in moneyflow if _code(row)}
        stock_meta = self._stock_metadata()
        changes = _valid_changes(daily)
        breadth = self._breadth(daily, changes)
        limit_summary, limit_codes = self._limit_structure(daily, limits_by_code)
        turnover = self._turnover(trade_date, daily)
        industries = self._industry_performance(daily, stock_meta, limit_codes)
        concepts = self._concept_performance(daily, limit_codes)
        indices = self._index_performance(trade_date)
        style = self._style_summary(daily, basic_by_code, stock_meta)
        capital = self._capital_summary(daily, money_by_code)
        technical = {
            "equal_weight_return": breadth["equal_weight_return"],
            "median_return": breadth["median_return"],
            "index_available_count": sum(item.data_status == DataStatus.AVAILABLE for item in indices),
            "status": "AVAILABLE" if changes else "NOT_AVAILABLE",
        }
        market_direction = _market_direction(breadth, float(self.config.get("flat_return_epsilon", 0.0001)))
        source_status = self._source_status(
            daily=daily,
            daily_basic=daily_basic,
            limits=limit_rows,
            moneyflow=moneyflow,
            stock_meta=stock_meta,
            industries=industries,
            concepts=concepts,
            indices=indices,
        )
        quality = _data_quality(source_status)
        watermark_hash = _watermark_hash([daily_path, basic_path, limit_path, moneyflow_path])
        metric_ids = _metric_ids(indices)
        payload = {
            "schema_version": str(self.config.get("schema_version", "market_daily_snapshot_v1")),
            "trade_date": trade_date,
            "decision_time": decision_time,
            "market_direction": market_direction,
            "indices": indices,
            "breadth": breadth,
            "turnover": turnover,
            "limit_structure": limit_summary,
            "industries": industries,
            "concepts": concepts,
            "style": style,
            "capital": capital,
            "technical": technical,
            "source_status": source_status,
            "metric_ids": metric_ids,
            "data_quality_score": quality,
            "dataset_watermark_hash": watermark_hash,
            "snapshot_hash": "pending",
        }
        snapshot = MarketDailySnapshotData.model_validate(payload)
        snapshot_hash = _hash_payload(snapshot.model_dump(mode="json", exclude={"snapshot_hash"}))
        return snapshot.model_copy(update={"snapshot_hash": snapshot_hash})

    def _trade_date_path(self, api_name: str, date_key: str) -> Path:
        return self.cache_root / "trade_date" / api_name / f"{date_key}.json"

    @staticmethod
    def _load_rows(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        value = json.loads(path.read_text(encoding="utf-8"))
        return [dict(row) for row in value] if isinstance(value, list) else []

    def _stock_metadata(self) -> dict[str, StockMaster]:
        return {normalize_ts_code(row.code): row for row in self.session.scalars(select(StockMaster))}

    def _breadth(self, daily: list[dict[str, Any]], changes: list[float]) -> dict[str, Any]:
        epsilon = float(self.config.get("flat_return_epsilon", 0.0001))
        valid_by_code = {_code(row): _change_ratio(row) for row in daily if _code(row) and _change_ratio(row) is not None}
        advancing = sum(value > epsilon for value in valid_by_code.values())
        declining = sum(value < -epsilon for value in valid_by_code.values())
        flat = sum(abs(value) <= epsilon for value in valid_by_code.values())
        missing = len(daily) - len(valid_by_code)
        valid_count = len(valid_by_code)
        return {
            "universe_count": len(daily),
            "valid_count": valid_count,
            "advancing_count": advancing,
            "declining_count": declining,
            "flat_count": flat,
            "suspended_count": sum(_float(row.get("vol")) == 0 for row in daily),
            "missing_count": missing,
            "advancing_ratio": advancing / valid_count if valid_count else 0.0,
            "declining_ratio": declining / valid_count if valid_count else 0.0,
            "advance_decline_ratio": advancing / declining if declining else None,
            "above_3_count": sum(value >= 0.03 for value in changes),
            "below_3_count": sum(value <= -0.03 for value in changes),
            "above_5_count": sum(value >= 0.05 for value in changes),
            "below_5_count": sum(value <= -0.05 for value in changes),
            "new_high_count": None,
            "new_low_count": None,
            "median_return": statistics.median(changes) if changes else None,
            "average_return": statistics.fmean(changes) if changes else None,
            "equal_weight_return": statistics.fmean(changes) if changes else None,
            "coverage_ratio": valid_count / len(daily) if daily else 0.0,
        }

    def _limit_structure(self, daily: list[dict[str, Any]], limits: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], set[str]]:
        limit_up_codes: set[str] = set()
        limit_down_codes: set[str] = set()
        one_price = 0
        bomb = 0
        for row in daily:
            code = _code(row)
            limit = limits.get(code)
            if not limit:
                continue
            close, high, low, open_price = (_float(row.get(key)) for key in ("close", "high", "low", "open"))
            up_limit, down_limit = _float(limit.get("up_limit")), _float(limit.get("down_limit"))
            if close is not None and up_limit is not None and close >= up_limit - 0.001:
                limit_up_codes.add(code)
                if all(value is not None and abs(value - up_limit) <= 0.001 for value in (open_price, high, low, close)):
                    one_price += 1
            if close is not None and down_limit is not None and close <= down_limit + 0.001:
                limit_down_codes.add(code)
            if high is not None and up_limit is not None and high >= up_limit - 0.001 and (close or 0) < up_limit - 0.001:
                bomb += 1
        attempts = len(limit_up_codes) + bomb
        return ({
            "limit_up_count": len(limit_up_codes),
            "limit_down_count": len(limit_down_codes),
            "non_one_price_limit_up_count": len(limit_up_codes) - one_price,
            "one_price_limit_up_count": one_price,
            "consecutive_limit_up_count": None,
            "highest_streak": None,
            "failed_limit_up_count": bomb,
            "failed_limit_up_ratio": bomb / attempts if attempts else 0.0,
            "prior_limit_up_performance": None,
            "status": "AVAILABLE" if limits else "NOT_AVAILABLE",
        }, limit_up_codes)

    def _turnover(self, trade_date: date, daily: list[dict[str, Any]]) -> dict[str, Any]:
        multiplier = float(self.config.get("amount_multiplier", 1000))
        current = sum(_float(row.get("amount")) or 0 for row in daily) * multiplier
        history = []
        for path in sorted((self.cache_root / "trade_date" / "daily").glob("*.json")):
            if path.stem > trade_date.strftime("%Y%m%d"):
                continue
            rows = self._load_rows(path)
            history.append((path.stem, sum(_float(row.get("amount")) or 0 for row in rows) * multiplier))
        history = history[-20:]
        previous = history[-2][1] if len(history) >= 2 else None
        five_values = [value for _, value in history[-5:]]
        average_5 = statistics.fmean(five_values) if five_values else None
        change_ratio = (current / previous - 1) if previous else None
        relative_5 = current / average_5 if average_5 else None
        percentile_20 = _percentile_rank([value for _, value in history], current)
        if relative_5 is None:
            state = "INSUFFICIENT_DATA"
        elif relative_5 >= 1.2:
            state = "STRONG_EXPANSION"
        elif relative_5 >= 1.05:
            state = "EXPANSION"
        elif relative_5 <= 0.8:
            state = "STRONG_CONTRACTION"
        elif relative_5 <= 0.95:
            state = "CONTRACTION"
        else:
            state = "NORMAL"
        sh_amount = sum((_float(row.get("amount")) or 0) for row in daily if _code(row).endswith(".SH")) * multiplier
        sz_amount = sum((_float(row.get("amount")) or 0) for row in daily if _code(row).endswith(".SZ")) * multiplier
        bj_amount = sum((_float(row.get("amount")) or 0) for row in daily if _code(row).endswith(".BJ")) * multiplier
        return {
            "shanghai_amount": sh_amount,
            "shenzhen_amount": sz_amount,
            "beijing_amount": bj_amount,
            "total_amount": current,
            "previous_amount": previous,
            "change_ratio": change_ratio,
            "average_5d_amount": average_5,
            "relative_to_5d": relative_5,
            "percentile_20d": percentile_20,
            "turnover_state": state,
            "history_days": len(history),
            "status": "AVAILABLE" if current > 0 else "NOT_AVAILABLE",
        }

    def _industry_performance(self, daily: list[dict[str, Any]], stock_meta: dict[str, StockMaster], limit_codes: set[str]) -> list[SectorPerformance]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in daily:
            meta = stock_meta.get(_code(row))
            if meta and meta.industry:
                grouped[str(meta.industry)].append(row)
        return _rank_sectors(
            grouped,
            "INDUSTRY",
            limit_codes,
            float(self.config.get("amount_multiplier", 1000)),
            include_all=True,
        )

    def _concept_performance(self, daily: list[dict[str, Any]], limit_codes: set[str]) -> list[SectorPerformance]:
        index_rows = self._load_rows(self.cache_root / "concept" / "ths_index" / "latest.json")
        member_rows = self._load_rows(self.cache_root / "concept" / "ths_member" / "latest.json")
        names = {str(row.get("ts_code")): str(row.get("name") or row.get("ts_code")) for row in index_rows}
        daily_by_code = {_code(row): row for row in daily if _code(row)}
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for member in member_rows:
            concept_code = str(member.get("ts_code") or "")
            row = daily_by_code.get(normalize_ts_code(str(member.get("con_code") or "")))
            if concept_code and row:
                grouped[f"{concept_code}|{names.get(concept_code, concept_code)}"].append(row)
        return _rank_sectors(grouped, "CONCEPT", limit_codes, float(self.config.get("amount_multiplier", 1000)))

    def _index_performance(self, trade_date: date) -> list[IndexPerformance]:
        configured = list(self.config.get("indices") or [])
        local_path = self.cache_root.parent / "market_review" / "index" / f"{trade_date:%Y%m%d}.json"
        local_rows = self._load_rows(local_path)
        date_key = trade_date.strftime("%Y%m%d")
        by_code = {
            normalize_ts_code(str(row.get("index_code") or row.get("ts_code") or "")): row
            for row in local_rows if _row_date(row) == date_key
        }
        results = []
        for item in configured:
            code = normalize_ts_code(str(item["index_code"]))
            row = by_code.get(code)
            if not row:
                results.append(IndexPerformance(index_code=code, index_name=str(item["index_name"])))
                continue
            close, pre_close = _float(row.get("close")), _float(row.get("pre_close"))
            change = (close / pre_close - 1) if close is not None and pre_close else _normalized_percent(row.get("pct_chg"))
            high, low = _float(row.get("high")), _float(row.get("low"))
            amplitude = ((high - low) / pre_close) if high is not None and low is not None and pre_close else None
            results.append(IndexPerformance(
                index_code=code,
                index_name=str(item["index_name"]),
                open=_float(row.get("open")), high=high, low=low, close=close, pre_close=pre_close,
                change_percent=change,
                amount=(_float(row.get("amount")) or 0) * float(self.config.get("amount_multiplier", 1000)),
                volume=_float(row.get("vol") or row.get("volume")),
                intraday_range_percent=amplitude,
                ma5=_float(row.get("ma5")), ma20=_float(row.get("ma20")), ma60=_float(row.get("ma60")),
                atr14=_float(row.get("atr14")),
                position_vs_ma5=_position(close, row.get("ma5")),
                position_vs_ma20=_position(close, row.get("ma20")),
                trend_state=_trend_state(change, close, row.get("ma5"), row.get("ma20")),
                data_status=DataStatus.AVAILABLE,
                source="LOCAL_INDEX_CACHE",
            ))
        return results

    @staticmethod
    def _style_summary(daily: list[dict[str, Any]], basic: dict[str, dict[str, Any]], stock_meta: dict[str, StockMaster]) -> dict[str, Any]:
        groups: dict[str, list[float]] = defaultdict(list)
        cap_rows = []
        for row in daily:
            code, change = _code(row), _change_ratio(row)
            if change is None:
                continue
            groups[_board(code)].append(change)
            mv = _float((basic.get(code) or {}).get("circ_mv"))
            if mv is not None:
                cap_rows.append((mv, change))
        board_returns = {name: statistics.fmean(values) for name, values in groups.items() if values}
        sorted_boards = sorted(board_returns.items(), key=lambda pair: pair[1], reverse=True)
        cap_rows.sort()
        midpoint = len(cap_rows) // 2
        small_return = statistics.fmean(change for _, change in cap_rows[:midpoint]) if midpoint else None
        large_return = statistics.fmean(change for _, change in cap_rows[midpoint:]) if cap_rows[midpoint:] else None
        observed = list(board_returns.values())
        return {
            "dominant_style": sorted_boards[0][0] if sorted_boards else "NOT_AVAILABLE",
            "weak_style": sorted_boards[-1][0] if sorted_boards else "NOT_AVAILABLE",
            "style_divergence_score": (max(observed) - min(observed)) if observed else 0.0,
            "board_returns": board_returns,
            "large_cap_return": large_return,
            "small_cap_return": small_return,
            "capitalization_sample_count": len(cap_rows),
            "status": "AVAILABLE" if observed else "NOT_AVAILABLE",
        }

    @staticmethod
    def _capital_summary(daily: list[dict[str, Any]], money: dict[str, dict[str, Any]]) -> dict[str, Any]:
        values = [_float(row.get("net_mf_amount")) for row in money.values()]
        values = [value for value in values if value is not None]
        coverage = len(values) / len(daily) if daily else 0.0
        return {
            "net_main_inflow": sum(values) * 1000 if values else None,
            "coverage": coverage,
            "source": "TUSHARE_MONEYFLOW_TRADE_DATE_CACHE" if values else None,
            "data_status": "AVAILABLE" if coverage >= 0.8 else "PARTIAL" if values else "NOT_AVAILABLE",
        }

    @staticmethod
    def _source_status(**datasets: Any) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in datasets.items():
            if name == "stock_meta":
                count = len(value)
            elif name == "indices":
                count = sum(item.data_status == DataStatus.AVAILABLE for item in value)
            else:
                count = len(value)
            result[name] = {"count": count, "status": "AVAILABLE" if count else "NOT_AVAILABLE", "source": "LOCAL_STRUCTURED_DATA"}
        return result


def load_market_review_config() -> dict[str, Any]:
    config_dir = Path(os.getenv("AI_TRADER_CONFIG_DIR", Path(__file__).resolve().parents[1] / "config"))
    payload = yaml.safe_load((config_dir / "market_review.yaml").read_text(encoding="utf-8")) or {}
    return dict(payload.get("market_review") or {})


def _rank_sectors(
    grouped: dict[str, list[dict[str, Any]]],
    sector_type: str,
    limit_codes: set[str],
    multiplier: float,
    *,
    include_all: bool = False,
) -> list[SectorPerformance]:
    rows = []
    for key, members in grouped.items():
        changes = [_change_ratio(row) for row in members]
        changes = [value for value in changes if value is not None]
        if not changes:
            continue
        code, _, name = key.partition("|") if "|" in key else (key, "", key)
        rows.append(SectorPerformance(
            sector_code=code,
            sector_name=name or code,
            sector_type=sector_type,
            change_percent=statistics.fmean(changes),
            advancing_ratio=sum(value > 0 for value in changes) / len(changes),
            limit_up_count=sum(_code(row) in limit_codes for row in members),
            amount=sum(_float(row.get("amount")) or 0 for row in members) * multiplier,
            member_count=len(changes),
        ))
    rows.sort(key=lambda item: (-item.change_percent, item.sector_code))
    if include_all:
        selected = rows
    else:
        selected = rows[:10]
        seen = {item.sector_code for item in selected}
        selected.extend(item for item in rows[-10:] if item.sector_code not in seen)
    return [item.model_copy(update={"rank": index}) for index, item in enumerate(selected, start=1)]


def _metric_ids(indices: list[IndexPerformance]) -> list[str]:
    ids = [
        "breadth.advancing_count", "breadth.declining_count", "breadth.flat_count",
        "breadth.advancing_ratio", "breadth.equal_weight_return", "breadth.median_return",
        "turnover.total_amount", "turnover.change_ratio", "turnover.turnover_state",
        "limits.limit_up_count", "limits.limit_down_count", "limits.failed_limit_up_ratio",
        "style.dominant_style", "style.style_divergence_score", "capital.net_main_inflow",
    ]
    ids.extend(f"index.{item.index_code}.change_percent" for item in indices if item.data_status == DataStatus.AVAILABLE)
    return ids


def _data_quality(status: dict[str, Any]) -> float:
    weights = {"daily": 40, "daily_basic": 10, "limits": 10, "moneyflow": 5, "stock_meta": 10, "industries": 5, "concepts": 5, "indices": 15}
    score = sum(weight for name, weight in weights.items() if status.get(name, {}).get("status") == "AVAILABLE")
    return float(score)


def _market_direction(breadth: dict[str, Any], epsilon: float) -> str:
    average = float(breadth.get("equal_weight_return") or 0)
    up_ratio = float(breadth.get("advancing_ratio") or 0)
    down_ratio = float(breadth.get("declining_ratio") or 0)
    if abs(average) <= epsilon * 5:
        return "FLAT"
    if average > 0 and up_ratio >= 0.52:
        return "UP"
    if average < 0 and down_ratio >= 0.52:
        return "DOWN"
    return "MIXED"


def _valid_changes(rows: Iterable[dict[str, Any]]) -> list[float]:
    return [value for row in rows if (value := _change_ratio(row)) is not None]


def _change_ratio(row: dict[str, Any]) -> float | None:
    value = _normalized_percent(row.get("pct_chg", row.get("change_percent")))
    if value is not None:
        return value
    close, pre_close = _float(row.get("close")), _float(row.get("pre_close"))
    return (close / pre_close - 1) if close is not None and pre_close else None


def _normalized_percent(value: Any) -> float | None:
    number = _float(value)
    return number / 100 if number is not None else None


def _code(row: dict[str, Any]) -> str:
    value = row.get("ts_code") or row.get("stock_code") or row.get("code") or ""
    return normalize_ts_code(str(value)) if value else ""


def _row_date(row: dict[str, Any]) -> str:
    return str(row.get("trade_date") or row.get("datetime") or "").replace("-", "")[:8]


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _position(close: float | None, average: Any) -> float | None:
    average_value = _float(average)
    return close / average_value - 1 if close is not None and average_value else None


def _trend_state(change: float | None, close: float | None, ma5: Any, ma20: Any) -> str:
    if change is None:
        return "INSUFFICIENT_DATA"
    if change >= 0.015:
        return "STRONG_UP"
    if change <= -0.015:
        return "STRONG_DOWN"
    ma5_value, ma20_value = _float(ma5), _float(ma20)
    if close is not None and ma5_value and ma20_value:
        if close > ma5_value > ma20_value:
            return "UP"
        if close < ma5_value < ma20_value:
            return "DOWN"
    return "SIDEWAYS"


def _board(code: str) -> str:
    plain = code.split(".")[0]
    if code.endswith(".BJ") or plain.startswith(("4", "8")):
        return "北交所"
    if plain.startswith("300"):
        return "创业板"
    if plain.startswith("688"):
        return "科创板"
    return "沪深主板"


def _percentile_rank(values: list[float], current: float) -> float | None:
    return sum(value <= current for value in values) / len(values) if values else None


def _watermark_hash(paths: list[Path]) -> str:
    payload = []
    for path in paths:
        payload.append({
            "name": path.as_posix(),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
        })
    return _hash_payload(payload)


def _hash_payload(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
