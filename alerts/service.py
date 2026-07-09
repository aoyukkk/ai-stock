from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from alerts.alert_store import GLOBAL_ALERT_STORE, AlertStore
from alerts.config import AlertRulesConfig, load_alert_rules_config
from alerts.schemas import AlertEvent
from alerts.trigger_rules import (
    detect_limit_up_down,
    detect_pending_order_near_fill,
    detect_price_move,
    detect_volume_abnormal,
)
from database.models.order_plan import OrderPlan
from database.models.trading import Position, TradeOrder
from database.session import get_session, init_db
from datasource.service import DataSourceService


class IntradayAlertService:
    def __init__(
        self,
        session=None,
        data_source_service: DataSourceService | None = None,
        config: AlertRulesConfig | None = None,
        alert_store: AlertStore | None = None,
    ) -> None:
        self.session = session
        self.data_source_service = data_source_service or DataSourceService()
        self.config = config or load_alert_rules_config()
        self.alert_store = alert_store or GLOBAL_ALERT_STORE
        self.config.validate()

    def scan_watch_targets(self, limit: int | None = None) -> list[AlertEvent]:
        session, own_session = self._session()
        try:
            query = select(OrderPlan).where(OrderPlan.status.in_(["DRAFT", "ACTIVE", "WATCH_ONLY"]))
            if limit is not None:
                query = query.limit(limit)
            plans = session.scalars(query).all()
            return self._alerts_for_stock_codes([plan.stock_code for plan in plans])
        finally:
            if own_session:
                session.close()

    def scan_virtual_orders(self) -> list[AlertEvent]:
        session, own_session = self._session()
        try:
            orders = session.scalars(select(TradeOrder).where(TradeOrder.status == "PENDING")).all()
            if not orders:
                return []
            quotes = {
                quote.stock_code: quote
                for quote in self.data_source_service.get_realtime_quotes([order.stock_code for order in orders])
            }
            alerts: list[AlertEvent] = []
            for order in orders:
                quote = quotes.get(order.stock_code)
                if quote is None or order.order_price is None:
                    continue
                alert = detect_pending_order_near_fill(order.order_price, quote.current_price, self.config, order.stock_code)
                if alert is not None:
                    alert.related_virtual_order_id = order.id
                    alerts.append(alert)
            self.alert_store.add_many(alerts)
            return alerts
        finally:
            if own_session:
                session.close()

    def scan_virtual_positions(self) -> list[AlertEvent]:
        session, own_session = self._session()
        try:
            positions = session.scalars(select(Position).where(Position.quantity > 0)).all()
            return self._alerts_for_stock_codes([position.stock_code for position in positions])
        finally:
            if own_session:
                session.close()

    def run_intraday_scan(self) -> list[AlertEvent]:
        alerts = []
        alerts.extend(self.scan_watch_targets())
        alerts.extend(self.scan_virtual_positions())
        alerts.extend(self.scan_virtual_orders())
        # De-duplicate by alert type/source/stock/relation for a compact API response.
        seen = set()
        unique: list[AlertEvent] = []
        for alert in alerts:
            key = (alert.stock_code, alert.alert_type, alert.related_virtual_order_id, alert.related_order_plan_id)
            if key not in seen:
                seen.add(key)
                unique.append(alert)
        self.alert_store.add_many([event for event in unique if event not in self.alert_store.recent(200)])
        return unique

    def recent_alerts(self, limit: int = 50) -> list[AlertEvent]:
        return self.alert_store.recent(limit)

    def config_summary(self) -> dict:
        return self.config.summary()

    def _alerts_for_stock_codes(self, stock_codes: list[str]) -> list[AlertEvent]:
        unique_codes = sorted(set(code for code in stock_codes if code))
        if not unique_codes:
            return []
        quotes = self.data_source_service.get_realtime_quotes(unique_codes)
        alerts: list[AlertEvent] = []
        for quote in quotes:
            price_alert = detect_price_move(quote.stock_code, quote.change_percent, self.config)
            if price_alert is not None:
                alerts.append(price_alert)
            trade_date = quote.quote_time.date()
            limit_price = self.data_source_service.get_limit_price(quote.stock_code, trade_date)
            limit_alert = detect_limit_up_down(quote.current_price, limit_price.limit_up_price, limit_price.limit_down_price, quote.stock_code)
            if limit_alert is not None:
                alerts.append(limit_alert)
            kline = self.data_source_service.get_kline(
                quote.stock_code,
                start_date=trade_date - timedelta(days=5),
                end_date=trade_date,
            )
            if kline:
                average_volume = int(sum(bar.volume for bar in kline) / len(kline))
                volume_alert = detect_volume_abnormal(quote.volume, average_volume, self.config, quote.stock_code)
                if volume_alert is not None:
                    alerts.append(volume_alert)
        self.alert_store.add_many(alerts)
        return alerts

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True
