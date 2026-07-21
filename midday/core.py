from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from database.models import (
    ManualSelectionRecord,
    OrderPlan,
    QuantRankResult,
    QuantRun,
    RunDataManifestRecord,
    TraderPositionSnapshot,
)
from post_close.gates import PositionTruthGate
from stock_codes import normalize_ts_code


SHANGHAI = ZoneInfo("Asia/Shanghai")
TERMINAL_ORDER_STATUSES = {"CANCELLED", "CANCELED", "EXPIRED", "FILLED", "INVALID", "REJECTED"}


class MiddayTimeGate:
    def __init__(self, *, start_after: str = "11:32", latest: str = "12:55") -> None:
        self.start_after = time.fromisoformat(start_after)
        self.latest = time.fromisoformat(latest)

    def evaluate(self, trade_date: date, decision_time: datetime) -> dict[str, Any]:
        local = decision_time.astimezone(SHANGHAI)
        market_session = market_session_at(local, trade_date)
        allowed = market_session == "MIDDAY_BREAK" and self.start_after <= local.time().replace(tzinfo=None) <= self.latest
        if allowed:
            status = "READY"
        elif market_session == "MIDDAY_BREAK" and local.time().replace(tzinfo=None) > self.latest:
            status = "MISSED_MIDDAY_WINDOW"
        else:
            status = "MIDDAY_SESSION_REQUIRED"
        return {
            "passed": allowed,
            "status": status,
            "decision_time": local,
            "market_session": market_session,
            "allowed_start_time": self.start_after.isoformat(timespec="minutes"),
            "latest_start_time": self.latest.isoformat(timespec="minutes"),
        }


class MiddayBaselineResolver:
    def __init__(self, session) -> None:
        self.session = session

    def resolve(self, trade_date: date, *, top_n: int = 100) -> dict[str, Any]:
        runs = self.session.scalars(select(QuantRun).where(
            QuantRun.base_market_trade_date < trade_date,
            QuantRun.status.in_(["COMPLETED", "SUCCESS"]),
            QuantRun.temporal_status.in_(["PASS", "PASS_WITH_WARNINGS"]),
            QuantRun.actionable.is_(True),
            QuantRun.no_llm_call_verified.is_(True),
            QuantRun.per_stock_api_call_count == 0,
        ).order_by(QuantRun.base_market_trade_date.desc(), QuantRun.created_at.desc())).all()
        for run in runs:
            manifest = self.session.scalar(select(RunDataManifestRecord).where(
                RunDataManifestRecord.manifest_id == run.data_manifest_id,
                RunDataManifestRecord.temporal_status.in_(["PASS", "PASS_WITH_WARNINGS"]),
                RunDataManifestRecord.actionable.is_(True),
            ))
            if manifest is None or run.scored_count < top_n:
                continue
            rows = list(self.session.scalars(select(QuantRankResult).where(
                QuantRankResult.quant_run_id == run.run_id,
                QuantRankResult.rank <= top_n,
            ).order_by(QuantRankResult.rank)))
            if len(rows) != top_n:
                continue
            return {
                "trade_date": run.base_market_trade_date,
                "quant_run_id": run.run_id,
                "manifest_id": manifest.manifest_id,
                "scored_count": run.scored_count,
                "factor_version": run.factor_version,
                "scoring_profile": run.config_snapshot.get("scoring_profile", "TUSHARE_BASELINE_V1"),
                "completed_at": run.updated_at,
                "rows": rows,
            }
        raise ValueError("MIDDAY_BASELINE_NOT_AVAILABLE")


class MiddayPoolResolver:
    PRIORITY = {"HUMAN_POSITION": 0, "AI_POSITION": 1, "ORDER_PLAN": 2, "MANUAL": 3, "BASE_TOP100": 4}

    def __init__(self, session) -> None:
        self.session = session

    def resolve(self, trade_date: date, baseline: dict[str, Any], *, maximum: int = 120, truth: dict[str, Any] | None = None) -> dict[str, Any]:
        source_codes: dict[str, set[str]] = {
            "BASE_TOP100": {normalize_ts_code(row.stock_code) for row in baseline["rows"]},
            "MANUAL": {normalize_ts_code(code) for code in self.session.scalars(select(ManualSelectionRecord.stock_code).where(ManualSelectionRecord.trade_date == trade_date))},
            "HUMAN_POSITION": set(),
            "AI_POSITION": set(),
            "ORDER_PLAN": set(),
        }
        positions = list(self.session.scalars(select(TraderPositionSnapshot).where(TraderPositionSnapshot.is_current.is_(True))))
        position_rows: dict[str, list[TraderPositionSnapshot]] = {}
        for row in positions:
            code = normalize_ts_code(row.stock_code)
            source = "HUMAN_POSITION" if row.account_scope == "HUMAN_REFERENCE" else "AI_POSITION"
            source_codes[source].add(code)
            position_rows.setdefault(code, []).append(row)
        for row in self.session.scalars(select(OrderPlan).where(OrderPlan.plan_date.in_([trade_date, baseline["trade_date"]]))):
            if str(row.status or "ACTIVE").upper() not in TERMINAL_ORDER_STATUSES:
                source_codes["ORDER_PLAN"].add(normalize_ts_code(row.stock_code))

        memberships: dict[str, set[str]] = {}
        for source, codes in source_codes.items():
            for code in codes:
                memberships.setdefault(code, set()).add(source)
        all_rank_rows = {
            normalize_ts_code(row.stock_code): row
            for row in self.session.scalars(select(QuantRankResult).where(
                QuantRankResult.quant_run_id == baseline["quant_run_id"],
            ))
        }
        ordered = sorted(memberships, key=lambda code: (
            min(self.PRIORITY[source] for source in memberships[code]),
            all_rank_rows[code].rank if code in all_rank_rows else 10**9,
            code,
        ))
        kept = ordered[:maximum]
        required = {code for code in ordered if memberships[code] & {"HUMAN_POSITION", "AI_POSITION", "ORDER_PLAN", "MANUAL"}}
        if not required.issubset(set(kept)):
            raise ValueError("MIDDAY_PRIORITY_POOL_TRUNCATION")
        truth = truth or {}
        truth_allowed = bool(truth.get("action_run_allowed"))
        items = []
        for code in kept:
            scopes = {row.account_scope for row in position_rows.get(code, [])}
            position_status = "HELD" if scopes else "NOT_HELD" if truth_allowed else "UNKNOWN"
            row = all_rank_rows.get(code)
            if row is None:
                continue
            items.append({
                "stock_code": code,
                "sources": sorted(memberships[code]),
                "position_status": position_status,
                "positions": position_rows.get(code, []),
                "base_quant_rank": row.rank,
                "base_quant_score": float(row.total_score),
            })
        raw_total = sum(len(codes) for codes in source_codes.values())
        canonical = [{"stock_code": item["stock_code"], "sources": item["sources"]} for item in items]
        return {
            "items": items,
            "counts": {key: len(value) for key, value in source_codes.items()},
            "raw_total": raw_total,
            "deduplicated_total": len(items),
            "duplicate_count": raw_total - len(memberships),
            "truncated_count": max(0, len(memberships) - len(items)),
            "pool_hash": hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        }


def market_session_at(value: datetime, trade_date: date) -> str:
    local = value.astimezone(SHANGHAI)
    if local.date() != trade_date or trade_date.weekday() >= 5:
        return "NON_TRADING_DAY"
    clock = local.time().replace(tzinfo=None)
    if clock < time(9, 30):
        return "PRE_MARKET"
    if clock <= time(11, 30):
        return "MORNING_SESSION"
    if clock < time(13, 0):
        return "MIDDAY_BREAK"
    if clock <= time(15, 0):
        return "AFTERNOON_SESSION"
    return "POST_MARKET"


def distribution(values: list[str]) -> dict[str, int]:
    return dict(Counter(values))
