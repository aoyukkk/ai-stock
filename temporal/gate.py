from __future__ import annotations

from datetime import date, datetime

from temporal.schemas import RunDataManifest, RunMode, RunTemporalContext, TemporalStatus


class TemporalConsistencyGate:
    @staticmethod
    def point_in_time_factor_legal(
        *,
        factor_trade_date: date,
        factor_available_at: datetime,
        base_market_trade_date: date,
        decision_time: datetime,
    ) -> bool:
        if factor_available_at.tzinfo is None or decision_time.tzinfo is None:
            return False
        return (
            factor_trade_date <= base_market_trade_date
            and factor_available_at <= decision_time
        )

    def evaluate(self, context: RunTemporalContext, manifest: RunDataManifest) -> RunDataManifest:
        reasons: list[str] = []
        warnings: list[str] = []
        if context.timezone != "Asia/Shanghai" or context.decision_time.tzinfo is None:
            reasons.append("INVALID_DECISION_TIME_TIMEZONE")
        if manifest.run_id != context.run_id:
            reasons.append("MANIFEST_RUN_MISMATCH")
        watermarks = {item.dataset_name: item for item in manifest.required_dataset_watermarks + manifest.optional_dataset_watermarks}
        mode = context.run_mode
        if mode is RunMode.RESEARCH_ONLY:
            status, actionable = TemporalStatus.PASS_WITH_WARNINGS, False
            warnings.append("RESEARCH_ONLY_NON_ACTIONABLE")
        elif mode is RunMode.INTRADAY_MONITOR:
            if context.realtime_snapshot_time is None:
                reasons.extend(["REALTIME_MARKET_DATA_REQUIRED", "STALE_MARKET_WITH_CURRENT_INFORMATION"])
            status, actionable = (TemporalStatus.BLOCKED, False) if reasons else (TemporalStatus.PASS, True)
        elif mode is RunMode.PRE_MARKET_RECHECK:
            self._require_dates(watermarks, ("daily", "daily_basic", "moneyflow"), context.base_market_trade_date, reasons)
            self._require_dates(watermarks, ("stk_limit", "adj_factor"), context.target_trade_date, reasons)
            status, actionable = (TemporalStatus.BLOCKED, False) if reasons else (TemporalStatus.PASS, True)
        elif mode is RunMode.POST_MARKET_PRELIMINARY:
            self._require_complete(watermarks, ("daily", "daily_basic"), context.base_market_trade_date, reasons)
            money = watermarks.get("moneyflow")
            if money is None or not money.is_complete or money.latest_trade_date != context.base_market_trade_date:
                warnings.append("MONEYFLOW_NOT_READY")
            if reasons:
                status, actionable = TemporalStatus.BLOCKED, False
            else:
                status, actionable = TemporalStatus.PROVISIONAL, False
        else:
            self._require_complete(watermarks, ("daily", "daily_basic", "moneyflow"), context.base_market_trade_date, reasons)
            if mode is RunMode.POST_MARKET_FINAL and context.market_session in {"MORNING_SESSION", "MIDDAY_BREAK", "AFTERNOON_SESSION"}:
                reasons.append("CURRENT_TRADE_DATE_NOT_COMPLETE")
            status, actionable = (TemporalStatus.BLOCKED, False) if reasons else (TemporalStatus.PASS, True)
        adj = watermarks.get("adj_factor")
        if adj and adj.factor_available_at and adj.factor_available_at > context.decision_time:
            warnings.append("POINT_IN_TIME_ADJUSTMENT_UNAVAILABLE")
        if reasons:
            status, actionable = TemporalStatus.BLOCKED, False
        context.temporal_status = status
        context.actionable = actionable
        return manifest.model_copy(update={
            "temporal_status": status, "actionable": actionable,
            "block_reasons": sorted(set(reasons)), "warnings": sorted(set(warnings)),
        })

    @staticmethod
    def _require_dates(watermarks, names, expected, reasons):
        for name in names:
            item = watermarks.get(name)
            if item is None or item.latest_trade_date != expected:
                reasons.append(f"{name.upper()}_TRADE_DATE_MISMATCH")

    @staticmethod
    def _require_complete(watermarks, names, expected, reasons):
        for name in names:
            item = watermarks.get(name)
            if item is None:
                reasons.append(f"{name.upper()}_WATERMARK_MISSING")
            elif item.latest_trade_date != expected:
                reasons.append(f"{name.upper()}_TRADE_DATE_MISMATCH")
            elif not item.is_complete:
                reasons.append(f"{name.upper()}_COVERAGE_INCOMPLETE")
