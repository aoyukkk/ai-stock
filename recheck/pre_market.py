from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from alerts.alert_store import GLOBAL_ALERT_STORE, AlertStore
from alerts.config import AlertRulesConfig, load_alert_rules_config
from alerts.trigger_rules import detect_auction_abnormal, detect_important_news
from database.models.order_plan import OrderPlan
from datasource.service import DataSourceService
from recheck.exceptions import OrderPlanNotFoundError
from recheck.reassessment import now_utc, persist_reassessment_log, reprice_from_latest
from recheck.schemas import OrderRecheckInput, OrderRecheckResult


class PreMarketRecheckEngine:
    def __init__(
        self,
        session,
        data_source_service: DataSourceService | None = None,
        config: AlertRulesConfig | None = None,
        alert_store: AlertStore | None = None,
    ) -> None:
        self.session = session
        self.data_source_service = data_source_service or DataSourceService()
        self.config = config or load_alert_rules_config()
        self.alert_store = alert_store or GLOBAL_ALERT_STORE
        self.config.validate()

    def recheck_order_plan(self, order_plan_id: int) -> OrderRecheckResult:
        plan = self.session.get(OrderPlan, order_plan_id)
        if plan is None:
            raise OrderPlanNotFoundError(f"Order plan not found: {order_plan_id}")
        result_input = self._build_input(plan)
        result = self._decide(result_input)
        persist_reassessment_log(self.session, result, trigger_type=result_input.trigger_type)
        self.alert_store.add_many(result.alert_events)
        return result

    def recheck_all_active_plans(self, limit: int | None = None) -> list[OrderRecheckResult]:
        query = select(OrderPlan).where(OrderPlan.status.in_(["DRAFT", "ACTIVE", "WATCH_ONLY"]))
        if limit is not None:
            query = query.limit(limit)
        plans = self.session.scalars(query).all()
        return [self.recheck_order_plan(plan.id) for plan in plans]

    def _build_input(self, plan: OrderPlan) -> OrderRecheckInput:
        quote = self.data_source_service.get_realtime_quotes([plan.stock_code])[0]
        trade_date = quote.quote_time.date()
        auction = self.data_source_service.get_pre_market_auction(plan.stock_code, trade_date)
        limit_price = self.data_source_service.get_limit_price(plan.stock_code, trade_date)
        news = self.data_source_service.get_latest_news(limit=20)
        related_news = [
            item
            for item in news
            if plan.stock_code in item.related_stocks or not item.related_stocks
        ]
        news_importance = max((item.importance for item in related_news), default=Decimal("0"))
        risk_level = _risk_level_from_plan(plan)
        trigger_type = "PRE_MARKET_RECHECK"
        return OrderRecheckInput(
            order_plan_id=plan.id,
            stock_code=plan.stock_code,
            old_recommended_price=plan.recommended_price,
            latest_price=quote.current_price,
            auction_price=auction.auction_price,
            auction_change_percent=auction.auction_change_percent,
            limit_up_price=limit_price.limit_up_price,
            limit_down_price=limit_price.limit_down_price,
            risk_level=risk_level,
            news_importance=news_importance,
            trigger_type=trigger_type,
        )

    def _decide(self, data: OrderRecheckInput) -> OrderRecheckResult:
        alerts = []
        auction_alert = detect_auction_abnormal(data.auction_change_percent, self.config, data.stock_code)
        if auction_alert is not None:
            auction_alert.related_order_plan_id = data.order_plan_id
            alerts.append(auction_alert)

        news_alert = self._important_negative_news_alert(data)
        if news_alert is not None:
            news_alert.related_order_plan_id = data.order_plan_id
            alerts.append(news_alert)

        high_threshold = Decimal(str(self.config.pre_market["high_open_cancel_threshold_percent"]))
        low_threshold = Decimal(str(self.config.pre_market["low_open_recheck_threshold_percent"]))
        if data.risk_level == "BLACK_SWAN":
            action = "BLOCK"
            reason = "Risk level BLACK_SWAN blocks this order plan."
            new_price = None
        elif news_alert is not None and news_alert.suggested_action == "BLOCK":
            action = "BLOCK"
            reason = "Important negative mock news triggered BLOCK."
            new_price = None
        elif data.risk_level == "HIGH":
            action = "WATCH_ONLY"
            reason = "Risk level HIGH requires watch-only advisory."
            new_price = data.old_recommended_price
        elif data.auction_change_percent >= high_threshold:
            action = "CANCEL"
            reason = "High-open auction exceeded cancel threshold."
            new_price = data.old_recommended_price
        elif data.auction_change_percent <= low_threshold:
            action = "REPRICE"
            reason = "Low-open auction exceeded reprice threshold."
            new_price = reprice_from_latest(data.latest_price, data.old_recommended_price)
        else:
            action = "KEEP"
            reason = "No configured pre-market risk trigger was hit."
            new_price = data.old_recommended_price

        return OrderRecheckResult(
            order_plan_id=data.order_plan_id,
            stock_code=data.stock_code,
            action=action,  # type: ignore[arg-type]
            old_recommended_price=data.old_recommended_price,
            new_recommended_price=new_price,
            reason=reason,
            risk_level=data.risk_level,
            alert_events=alerts,
            created_at=now_utc(),
        )

    def _important_negative_news_alert(self, data: OrderRecheckInput):
        class _NewsLike:
            importance = data.news_importance
            sentiment = "NEGATIVE"
            related_stocks = [data.stock_code]
            title = "Mock important negative news for pre-market recheck"

        threshold = Decimal(str(self.config.news.get("major_negative_threshold", self.config.news["importance_threshold"])))
        if data.news_importance >= threshold:
            return detect_important_news(_NewsLike(), self.config)
        return None


def _risk_level_from_plan(plan: OrderPlan) -> str:
    conditions = plan.cancel_conditions or {}
    risk_levels = conditions.get("risk_level_block") if isinstance(conditions, dict) else None
    if isinstance(risk_levels, list) and "BLACK_SWAN" in risk_levels and plan.status == "BLOCKED":
        return "BLACK_SWAN"
    if plan.status == "WATCH_ONLY":
        return "HIGH"
    return "LOW"
