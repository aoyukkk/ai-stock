from __future__ import annotations

import time
import uuid
from collections import Counter
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.core.config import get_app_config
from backend.core.runtime_paths import tushare_cache_root
from database.models import MiddayV22AfternoonResult, MiddayV22AfternoonRun, MiddayV22Result, MiddayV22Run, StockMaster
from entry_timing.market_emotion import MarketEmotionEngine
from entry_timing.service import TradeDateTimingCache
from entry_timing.v22 import IntradayEntryTriggerEngine, MarketRegimeV2Engine, RegimeV2Result
from midday.asof import AsOfMarketDataResolver, cross_section_breadth
from midday.core import MiddayBaselineResolver, SHANGHAI
from midday.provider import MiddayIFindCollector
from midday.v22_service import MiddayV22OneShotService, V22Failure, _volume_ratio
from stock_codes import normalize_ts_code


class MiddayV22AfternoonRecheckService:
    def __init__(self, session, *, output_root: Path) -> None:
        self.session = session
        self.output_root = Path(output_root)
        self.app = get_app_config()
        self.mid_cfg = self.app.config_files["midday_recommendation"]["midday_recommendation"]
        helper = MiddayV22OneShotService(session, output_root=output_root)
        self.v21, self.v22, self.helper = helper.v21, helper.v22, helper

    def run(self, trade_date: date, stocks: list[str], *, now: datetime | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        observed = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        run = self._start_run(trade_date, observed)
        try:
            if observed.date() != trade_date or observed.time() < clock_time(13, 0):
                raise V22Failure("PREFLIGHT", "AFTERNOON_SESSION_NOT_STARTED", "Real afternoon data is not available before 13:00 Asia/Shanghai")
            if self.app.real_trading_enabled:
                raise V22Failure("PREFLIGHT", "REAL_TRADING_ENABLED", "ENABLE_REAL_TRADING must remain false")
            midday_run = self.session.scalar(select(MiddayV22Run).where(
                MiddayV22Run.trade_date == trade_date, MiddayV22Run.status.in_(["SUCCESS", "EMPTY_POOL", "PARTIAL"]),
            ).order_by(MiddayV22Run.created_at.desc()))
            if midday_run is None:
                raise V22Failure("PREFLIGHT", "MIDDAY_V22_RUN_MISSING", "Completed V2.2 midday run is required")
            run.midday_run_id = midday_run.run_id; run.previous_midday_regime = midday_run.midday_regime
            all_rows = list(self.session.scalars(select(MiddayV22Result).where(MiddayV22Result.run_id == midday_run.run_id)))
            prior = {normalize_ts_code(row.stock_code): row.payload_json for row in all_rows}
            target_codes = list(dict.fromkeys(normalize_ts_code(code) for code in stocks))
            missing = [code for code in target_codes if code not in prior]
            if missing:
                raise V22Failure("PREFLIGHT", "RECHECK_STOCK_NOT_IN_MIDDAY_RUN", ",".join(missing))
            baseline = MiddayBaselineResolver(self.session).resolve(trade_date, top_n=100)
            all_codes = list(dict.fromkeys(normalize_ts_code(row.stock_code) for row in all_rows if row.pool_type == "AI_POOL"))
            bars = TradeDateTimingCache(tushare_cache_root()).load(baseline["trade_date"], set(all_codes))["bars"]
            previous_closes = {code: float(bars[code][-1]["close"]) if bars.get(code) else None for code in all_codes}
            collector = MiddayIFindCollector(self.session, self.app, self.mid_cfg["ifind"])
            if not collector.gate()["passed"]:
                raise V22Failure("PREFLIGHT", "IFIND_AUTH_FAILED", "Verified iFinD Shadow gate failed")
            collector._ensure_provider(); provider = collector.provider
            run.current_stage = "DATA_RESOLUTION"; self.session.commit()
            realtime_requested = datetime.now(SHANGHAI); realtime = provider.get_realtime(target_codes); realtime_received = datetime.now(SHANGHAI)
            realtime_by = {normalize_ts_code(row.stock_code): row for row in realtime}
            resolver_config = {**self.mid_cfg["ifind"], "allow_persisted_snapshot": False}
            resolver = AsOfMarketDataResolver(self.session, provider, resolver_config)
            index_codes = self.helper._index_codes()
            previous_index = {row.index_code: float(row.close) for row in provider.get_index_daily(index_codes, baseline["trade_date"], baseline["trade_date"])}
            cutoff = _afternoon_cutoff(observed)
            index_snap, _ = resolver.resolve(trade_date, cutoff, index_codes, previous_closes=previous_index, batch_size=5)
            stock_snap, stock_series = resolver.resolve(trade_date, cutoff, all_codes, previous_closes=previous_closes, batch_size=5)
            run.provider_audit_json = [{"batch_id": "realtime-targets", "codes_requested": target_codes, "codes_returned": list(realtime_by), "rows": len(realtime), "requested_at": realtime_requested.isoformat(), "received_at": realtime_received.isoformat(), "read_only": True}, *resolver.audit]; run.counts_json = {"stocks_requested": len(target_codes), "stocks_with_afternoon_bar": sum(bool(stock_snap.get(code) and stock_snap[code].last_eligible_bar_time and datetime.fromisoformat(stock_snap[code].last_eligible_bar_time).time() >= clock_time(13, 0)) for code in target_codes), "provider_calls": resolver.provider_calls + 2}; self.session.commit()
            if any(code not in stock_snap or not stock_snap[code].last_eligible_bar_time or datetime.fromisoformat(stock_snap[code].last_eligible_bar_time).time() < clock_time(13, 0) for code in target_codes):
                raise V22Failure("DATA_RESOLUTION", "AFTERNOON_DATA_NOT_AVAILABLE", "Target securities have no legal bar at or after 13:00")
            masters = {normalize_ts_code(row.code): row for row in self.session.scalars(select(StockMaster))}
            valid = {code: row for code, row in stock_snap.items() if row.data_quality in {"VALID_EXACT", "VALID_NEAR_CUTOFF", "PARTIAL"}}
            breadth = cross_section_breadth(valid); industries = self.helper._industries(valid, masters)
            emotion = MarketEmotionEngine(self.v21).evaluate({"breadth": breadth, "limit_structure": {"limit_up_count": sum((row.change_pct_to_cutoff or 0) >= .095 for row in valid.values()), "limit_down_count": sum((row.change_pct_to_cutoff or 0) <= -.095 for row in valid.values())}})
            history = self.helper._regime_history(trade_date)
            history.append(RegimeV2Result(midday_run.previous_regime, midday_run.midday_regime or "REPAIR", midday_run.midday_regime or "REPAIR", ["MIDDAY_CHECKPOINT"], int((midday_run.checkpoint_json or {}).get("cooldown", 1)), 0, float((midday_run.checkpoint_json or {}).get("regime_confidence", .75))))
            regime = MarketRegimeV2Engine(self.v22).evaluate({"breadth": breadth, "limit_structure": {"limit_down_count": sum((row.change_pct_to_cutoff or 0) <= -.095 for row in valid.values())}, "industries": industries, "market_emotion_score": emotion.market_emotion_score}, history)
            run.current_stage = "TRIGGER"; run.afternoon_regime = regime.current_state; self.session.commit()
            sector = {row["sector_name"]: row for row in industries}; results = []
            for code in target_codes:
                old = prior[code]; snap = stock_snap[code]; series = stock_series.get(code, [])
                sector_row = sector.get(old.get("industry"), {}); sector_return = sector_row.get("change_percent")
                trigger_bars = self.helper._trigger_bars(series, snap, previous_closes.get(code))
                trigger = IntradayEntryTriggerEngine(self.v22).evaluate(strategy_id=old.get("strategy_id", "UNCLASSIFIED"), regime=regime.current_state, bars=trigger_bars, sector_return=sector_return)
                plan = dict(old.get("afternoon_recheck") or {}); afternoon_vwap = _session_vwap(series, clock_time(13, 0)); plan.update({"current_price": snap.close_at_cutoff, "afternoon_vwap": afternoon_vwap, "recheck_status": "COMPLETED", "unmet_conditions": list(trigger.reasons)})
                quote = realtime_by.get(code); llm_clear = _llm_clear(old); maximum = plan.get("maximum_acceptable_price"); invalidation = plan.get("invalidation_price"); stop = plan.get("stop_loss_reference")
                conditions = {"regime_stable": regime.current_state in {"REPAIR", "ROTATION", "RISK_ON"}, "price_acceptable": maximum is not None and snap.close_at_cutoff <= maximum, "volume_confirmed": "VOLUME_NOT_CONFIRMED" not in trigger.reasons, "sector_relative_confirmed": "SECTOR_RELATIVE_STRENGTH_NOT_CONFIRMED" not in trigger.reasons, "structure_valid": snap.vwap_to_cutoff is not None and snap.close_at_cutoff >= snap.vwap_to_cutoff and (invalidation is None or snap.close_at_cutoff >= invalidation), "no_new_hard_risk": trigger.status != "TRIGGER_REJECTED" and not old.get("block_reasons"), "llm_not_blocked": llm_clear}
                prior_layer = old.get("result_layer", "BLOCKED"); result_layer, recheck_status = _resolve_recheck_layer(prior_layer, trigger.status, conditions, current_price=snap.close_at_cutoff, stop_loss=stop)
                plan["recheck_status"] = recheck_status
                vwap_status="PASS" if snap.vwap_to_cutoff and snap.close_at_cutoff>=snap.vwap_to_cutoff and (afternoon_vwap is None or snap.close_at_cutoff>=afternoon_vwap) else "FAIL"
                payload = {"stock_code": code, "stock_name": old.get("stock_name"), "prior_layer": prior_layer, "result_layer": result_layer, "strategy_id": old.get("strategy_id"), "strategy_source": old.get("strategy_source"), "live_strategy_status": old.get("live_strategy_status"), "previous_midday_regime": midday_run.midday_regime, "afternoon_regime": regime.current_state, "current_price": snap.close_at_cutoff, "realtime_snapshot": quote.model_dump(mode="json") if quote else None, "minute_data_quality": snap.data_quality, "last_eligible_bar_time": snap.last_eligible_bar_time, "five_minute_structure": _five_minute_structure(series), "afternoon_vwap": afternoon_vwap, "trigger_status": trigger.status, "trigger_reasons": trigger.reasons, "trigger_scores": trigger.scores, "volume_confirmation": trigger.scores.get("volume_confirmation"), "vwap_status": vwap_status, "sector_relative_status": "PASS" if conditions["sector_relative_confirmed"] else "FAIL", "upgrade_conditions": conditions, "price_plan": plan, "real_orders": 0, "virtual_orders": 0}
                results.append(payload)
            audit = list(resolver.audit); audit.insert(0, {"batch_id": "realtime-targets", "codes_requested": target_codes, "codes_returned": list(realtime_by), "rows": len(realtime), "requested_at": realtime_requested.isoformat(), "received_at": realtime_received.isoformat(), "read_only": True})
            counts = {"stocks_rechecked": len(results), "result_layers": dict(Counter(row["result_layer"] for row in results)), "trigger": dict(Counter(row["trigger_status"] for row in results)), "provider_calls": resolver.provider_calls + 2, "real_orders": 0, "virtual_orders": 0}
            final_status = "AFTERNOON_RECHECK_COMPLETE"; report = {"run_id": run.run_id, "midday_run_id": midday_run.run_id, "trade_date": trade_date.isoformat(), "recheck_time": observed.isoformat(), "status": final_status, "previous_midday_regime": midday_run.midday_regime, "afternoon_regime": regime.current_state, "regime_reasons": regime.state_reasons, "breadth": breadth, "results": results, "counts": counts, "provider_audit": audit, "real_orders": 0, "virtual_orders": 0, "scheduler": False}
            run.status = final_status; run.current_stage = "COMPLETED"; run.counts_json = counts; run.provider_audit_json = audit; run.completed_at = datetime.now(timezone.utc)
            for row in results: self.session.add(MiddayV22AfternoonResult(run_id=run.run_id, stock_code=row["stock_code"], stock_name=row.get("stock_name"), prior_layer=row["prior_layer"], result_layer=row["result_layer"], trigger_status=row["trigger_status"], payload_json=row))
            self.session.commit()
            from midday.v22_afternoon_export import export_v22_afternoon
            paths = export_v22_afternoon(self.output_root, run, report); run.output_paths_json = paths; self.session.commit()
            return {**report, "output_paths": paths, "execution_ms": round((time.perf_counter()-started)*1000)}
        except Exception as exc:
            failure = exc if isinstance(exc, V22Failure) else V22Failure(run.current_stage, type(exc).__name__, str(exc))
            run.status = "FAILED"; run.current_stage = "FAILED"; run.failure_stage = failure.stage; run.error_code = failure.code; run.error_message = failure.message; run.completed_at = datetime.now(timezone.utc); self.session.commit()
            return {"run_id": run.run_id, "status": "FAILED", "failure_stage": failure.stage, "error_code": failure.code, "error_message": failure.message, "real_orders": 0, "virtual_orders": 0, "scheduler": False, "execution_ms": round((time.perf_counter()-started)*1000)}

    def _start_run(self, trade_date: date, observed: datetime) -> MiddayV22AfternoonRun:
        run = MiddayV22AfternoonRun(run_id=f"midday-v22-recheck-{uuid.uuid4().hex[:16]}", midday_run_id="PENDING", trade_date=trade_date, recheck_time=observed, status="RUNNING", current_stage="PREFLIGHT", counts_json={}, provider_audit_json=[], output_paths_json={}, real_orders=0, virtual_orders=0, scheduler_enabled=False)
        self.session.add(run); self.session.commit(); return run


def _session_vwap(rows: list[dict[str, Any]], start: clock_time) -> float | None:
    eligible = [row for row in rows if datetime.fromisoformat(row["bar_time"]).time() >= start]
    volume = sum(float(row.get("volume") or 0) for row in eligible); amount = sum(float(row.get("amount") or 0) for row in eligible)
    return amount / volume if volume and amount else None


def _afternoon_cutoff(observed:datetime)->clock_time:
    return min(observed.time().replace(tzinfo=None,microsecond=0),clock_time(15,0))


def _five_minute_structure(rows: list[dict[str, Any]]) -> dict[str, Any]:
    afternoon = [row for row in rows if datetime.fromisoformat(row["bar_time"]).time() >= clock_time(13, 0)]
    groups = [afternoon[index:index+5] for index in range(0, len(afternoon), 5) if len(afternoon[index:index+5]) == 5]
    if not groups: return {"bar_count": 0, "trend": "DATA_INSUFFICIENT"}
    closes = [float(group[-1]["close"]) for group in groups]
    return {"bar_count": len(groups), "first_close": closes[0], "latest_close": closes[-1], "return": closes[-1]/closes[0]-1 if closes[0] else None, "trend": "UP" if closes[-1] > closes[0] else "DOWN" if closes[-1] < closes[0] else "FLAT"}


def _llm_clear(row: dict[str, Any]) -> bool:
    decisions = [value.get("decision") for value in (row.get("flash"), row.get("pro")) if isinstance(value, dict)]
    return all(value not in {"REJECT", "BLOCK"} for value in decisions)


def _resolve_recheck_layer(prior_layer: str, trigger_status: str, conditions: dict[str, bool], *, current_price: float, stop_loss: float | None) -> tuple[str, str]:
    if prior_layer != "AFTERNOON_WATCH": return prior_layer, "RETAINED"
    if trigger_status == "ENTRY_TRIGGERED" and all(conditions.values()): return "BUY_READY", "UPGRADED"
    if trigger_status == "TRIGGER_REJECTED" or (stop_loss is not None and current_price < stop_loss): return "BLOCKED", "REJECTED"
    return "AFTERNOON_WATCH", "WATCH"
