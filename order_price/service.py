from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agents.committee import AICommitteeService
from agents.schemas import CommitteeStockResult
from database.session import get_session
from datasource.service import DataSourceService
from order_price.config import OrderPriceConfig, load_order_price_config
from order_price.order_plan_generator import generate_order_plan
from order_price.persistence import persist_order_plans
from order_price.schemas import OrderPlanDraft, OrderPriceInput, OrderPriceRanking


class OrderPriceService:
    def __init__(
        self,
        committee_service: AICommitteeService | None = None,
        data_source_service: DataSourceService | None = None,
        config: OrderPriceConfig | None = None,
    ) -> None:
        self.config = config or load_order_price_config()
        self.committee_service = committee_service or AICommitteeService()
        self.data_source_service = data_source_service or DataSourceService()

    def generate_order_plans(
        self,
        input_top_n: int | None = None,
        persist: bool = False,
        session=None,
    ) -> OrderPriceRanking:
        self.config.validate()
        committee_ranking = self.committee_service.run_committee(
            input_top_n=input_top_n,
            final_top_n=input_top_n,
            persist=False,
        )
        candidates = [
            result
            for result in committee_ranking.results
            if result.recommendation in {"STRONG_WATCH", "WATCH"}
        ]
        plans = [self._build_plan(result) for result in candidates]
        ranking = OrderPriceRanking(
            generated_at=datetime.now(timezone.utc),
            input_count=len(candidates),
            returned_count=len(plans),
            plans=plans,
        )

        if persist:
            own_session = session is None
            db_session = session or get_session()
            try:
                persist_order_plans(db_session, ranking)
            finally:
                if own_session:
                    db_session.close()
        return ranking

    def config_summary(self) -> dict:
        self.config.validate()
        return self.config.summary()

    def _build_plan(self, result: CommitteeStockResult) -> OrderPlanDraft:
        quotes = self.data_source_service.get_realtime_quotes([result.stock_code])
        quote = quotes[0]
        trade_date = quote.quote_time.date()
        limit_price = self.data_source_service.get_limit_price(result.stock_code, trade_date)
        capital_flow = self.data_source_service.get_capital_flow(result.stock_code)
        lookback_days = max(self.config.atr_window, 20) - 1
        kline_bars = self.data_source_service.get_kline(
            result.stock_code,
            start_date=trade_date - timedelta(days=lookback_days),
            end_date=trade_date,
            frequency="1d",
        )

        context = OrderPriceInput(
            stock_code=result.stock_code,
            stock_name=result.stock_name,
            industry=result.industry,
            side="BUY",
            committee_score=result.final_score,
            recommendation=result.recommendation,
            risk_level=result.risk_level,
            confidence=result.confidence,
            previous_close=limit_price.previous_close,
            latest_price=quote.current_price,
            limit_up_price=limit_price.limit_up_price,
            limit_down_price=limit_price.limit_down_price,
            kline_bars=kline_bars,
            volume=quote.volume,
            amount=quote.amount,
            turnover_rate=capital_flow.turnover_rate,
            news_score=result.news_score,
            emotion_score=result.emotion_score,
            capital_score=result.capital_score,
        )
        return generate_order_plan(context, self.config, plan_date=trade_date)
