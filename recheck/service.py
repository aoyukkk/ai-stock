from __future__ import annotations

from backend.core.exceptions import AppException
from database.session import get_session, init_db
from recheck.exceptions import OrderPlanNotFoundError
from recheck.pre_market import PreMarketRecheckEngine
from alerts.service import IntradayAlertService
from alerts.config import load_alert_rules_config


class RecheckService:
    def __init__(self, session=None) -> None:
        self.session = session
        self.config = load_alert_rules_config()
        self.config.validate()

    def run_pre_market(self, limit: int | None = None):
        session, own_session = self._session()
        try:
            return PreMarketRecheckEngine(session=session, config=self.config).recheck_all_active_plans(limit=limit)
        finally:
            if own_session:
                session.close()

    def recheck_order_plan(self, order_plan_id: int):
        session, own_session = self._session()
        try:
            return PreMarketRecheckEngine(session=session, config=self.config).recheck_order_plan(order_plan_id)
        except OrderPlanNotFoundError as exc:
            raise AppException("ORDER_PLAN_NOT_FOUND", str(exc), status_code=404) from exc
        finally:
            if own_session:
                session.close()

    def run_intraday_scan(self):
        return IntradayAlertService(session=self.session, config=self.config).run_intraday_scan()

    def config_summary(self) -> dict:
        return self.config.summary()

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True
