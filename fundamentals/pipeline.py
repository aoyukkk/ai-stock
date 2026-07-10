from __future__ import annotations

from datetime import datetime, timezone

from fundamentals.cache import ReportPeriodCache
from fundamentals.profile import TushareFundamentalProfile, TushareFundamentalProfileBuilder
from fundamentals.revision import select_latest_revisions
from fundamentals.period_planner import FinancialPeriodPlanner


class CachedTushareProfileService:
    def __init__(self, cache: ReportPeriodCache | None = None) -> None:
        self.cache = cache or ReportPeriodCache()

    def latest_cached_period(self) -> str | None:
        periods = set()
        for interface in ("income", "balancesheet", "cashflow", "fina_indicator"):
            root = self.cache.root / interface
            if root.exists():
                periods.update(path.name for path in root.iterdir() if path.is_dir())
        return max(periods) if periods else None

    def build(self, stock_code: str, period: str | None = None, decision_time: datetime | None = None) -> TushareFundamentalProfile:
        lookup_code = _canonical_ts_code(stock_code)
        decision_time = decision_time or datetime.now(timezone.utc)
        periods = [period] if period else self._cached_periods()
        if not periods:
            return TushareFundamentalProfileBuilder().build(stock_code, as_of_time=decision_time)
        identity_period = self._latest_identity_period() or max(periods)
        stock_payload = self.cache.read("stock_basic", identity_period, {"period": identity_period})
        stock_basic = next(
            (row for row in (stock_payload or {}).get("records", []) if row.get("ts_code") == lookup_code),
            {},
        )
        companies = []
        for exchange in ("SSE", "SZSE", "BSE"):
            payload = self.cache.read("stock_company", identity_period, {"period": identity_period, "exchange": exchange})
            companies.extend((payload or {}).get("records", []))
        company = next((row for row in companies if row.get("ts_code") == lookup_code), {})
        rows, selections = {}, {}
        for interface in ("income", "balancesheet", "cashflow", "fina_indicator"):
            records_by_period = {}
            for candidate_period in periods:
                payload = self.cache.read(interface, candidate_period, {"period": candidate_period})
                if payload:
                    records_by_period[candidate_period] = payload.get("records", [])
            rows[interface], selections[interface] = FinancialPeriodPlanner().select_latest(records_by_period, lookup_code, decision_time)
        business = []
        for business_type in ("P", "I", "D"):
            for candidate_period in periods:
                payload = self.cache.read("mainbz", candidate_period, {"period": candidate_period, "type": business_type})
                for original in (payload or {}).get("records", []):
                    if original.get("ts_code") == lookup_code:
                        business.append({**original, "business_type": business_type})
        selection = max(
            (item for item in selections.values() if item.get("latest_financial_period")),
            key=lambda item: item.get("latest_financial_period", ""),
            default={},
        )
        return TushareFundamentalProfileBuilder().build(
            stock_code,
            stock_basic=stock_basic,
            company=company,
            income=rows["income"],
            balance=rows["balancesheet"],
            cashflow=rows["cashflow"],
            indicator=rows["fina_indicator"],
            main_business=business,
            as_of_time=decision_time,
            financial_selection=selection,
        )

    def _cached_periods(self) -> list[str]:
        periods = set()
        for interface in ("income", "balancesheet", "cashflow", "fina_indicator"):
            root = self.cache.root / interface
            if root.exists():
                periods.update(path.name for path in root.iterdir() if path.is_dir() and path.name.isdigit())
        return sorted(periods, reverse=True)

    def _latest_identity_period(self) -> str | None:
        root = self.cache.root / "stock_basic"
        periods = [path.name for path in root.iterdir() if path.is_dir() and path.name.isdigit()] if root.exists() else []
        return max(periods) if periods else None


def _canonical_ts_code(value: str) -> str:
    text = str(value).upper()
    if "." in text:
        return text
    code = text.zfill(6)
    if code.startswith(("4", "8", "920")): return f"{code}.BJ"
    if code.startswith("6"): return f"{code}.SH"
    return f"{code}.SZ"
