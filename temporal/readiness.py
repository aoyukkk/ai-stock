from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from database.session import get_session, init_db
from temporal.context import TemporalContextFactory
from temporal.gate import TemporalConsistencyGate
from temporal.repository import TemporalRepository
from temporal.schemas import RunDataManifest, RunMode, TemporalStatus
from temporal.watermarks import DatasetWatermarkService


class DataReadinessService:
    def __init__(self, context_factory=None, watermark_service=None) -> None:
        self.context_factory = context_factory or TemporalContextFactory()
        self.watermarks = watermark_service or DatasetWatermarkService()

    def check(
        self,
        run_mode: RunMode,
        decision_time: datetime | None = None,
        *,
        base_market_trade_date=None,
        target_trade_date=None,
        allow_provisional: bool = False,
        persist: bool = True,
    ) -> tuple:
        context = self.context_factory.create(
            run_mode, decision_time,
            base_market_trade_date=base_market_trade_date,
            target_trade_date=target_trade_date,
            allow_provisional=allow_provisional,
        )
        required_names = {
            RunMode.POST_MARKET_FINAL: ("daily", "daily_basic", "moneyflow"),
            RunMode.POST_MARKET_PRELIMINARY: ("daily", "daily_basic", "moneyflow"),
            RunMode.PRE_MARKET_RECHECK: ("daily", "daily_basic", "moneyflow", "stk_limit", "adj_factor"),
            RunMode.HISTORICAL_REPLAY: ("daily", "daily_basic", "moneyflow"),
            RunMode.INTRADAY_MONITOR: ("daily", "daily_basic"),
            RunMode.RESEARCH_ONLY: (),
        }[run_mode]
        watermarks = []
        for name in required_names:
            requested = context.target_trade_date if run_mode is RunMode.PRE_MARKET_RECHECK and name in {"stk_limit", "adj_factor"} else context.base_market_trade_date
            watermarks.append(self.watermarks.trade_date_watermark(name, requested))
        optional = []
        for name in ("stk_limit", "adj_factor"):
            if name not in required_names and context.base_market_trade_date:
                optional.append(self.watermarks.trade_date_watermark(name, context.base_market_trade_date))
        manifest = RunDataManifest(
            id=f"manifest-{uuid4().hex}", run_id=context.run_id, run_mode=run_mode,
            decision_time=context.decision_time, base_market_trade_date=context.base_market_trade_date,
            target_trade_date=context.target_trade_date, news_cutoff_time=context.news_cutoff_time,
            fundamental_cutoff_time=context.fundamental_cutoff_time,
            required_dataset_watermarks=watermarks, optional_dataset_watermarks=optional,
            temporal_status=TemporalStatus.BLOCKED, actionable=False, created_at=context.created_at,
        )
        manifest = TemporalConsistencyGate().evaluate(context, manifest)
        if persist:
            init_db(); session = get_session()
            try: TemporalRepository(session).save_manifest(manifest)
            finally: session.close()
        return context, manifest

    @staticmethod
    def payload(context, manifest) -> dict:
        from fundamentals.pipeline import CachedTushareProfileService
        return {
            "decision_time": context.decision_time.isoformat(), "market_session": context.market_session,
            "requested_run_mode": context.run_mode.value,
            "base_market_trade_date": context.base_market_trade_date.isoformat() if context.base_market_trade_date else None,
            "target_trade_date": context.target_trade_date.isoformat() if context.target_trade_date else None,
            "latest_completed_trade_date": context.latest_completed_trade_date.isoformat() if context.latest_completed_trade_date else None,
            "fundamental_latest_period": CachedTushareProfileService().latest_cached_period(),
            "dataset_watermarks": {item.dataset_name: item.model_dump(mode="json") for item in manifest.required_dataset_watermarks + manifest.optional_dataset_watermarks},
            "temporal_status": manifest.temporal_status.value, "actionable": manifest.actionable,
            "block_reasons": manifest.block_reasons, "warnings": manifest.warnings,
            "allowed_alternative_modes": ["RESEARCH_ONLY", "HISTORICAL_REPLAY"] if not manifest.actionable else [],
            "run_id": context.run_id, "run_data_manifest_id": manifest.id,
        }
