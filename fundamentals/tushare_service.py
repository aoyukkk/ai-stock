from __future__ import annotations

from typing import Any

from datasource.tushare_provider import TushareMarketDataProvider
from fundamentals.cache import ReportPeriodCache


DEFAULT_INTERFACES = (
    "stock_basic",
    "stock_company",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
    "mainbz",
    "forecast",
    "express",
    "fina_audit",
    "disclosure_date",
    "dividend",
)


class TushareFundamentalBatchService:
    def __init__(
        self,
        provider: TushareMarketDataProvider | None = None,
        cache: ReportPeriodCache | None = None,
    ) -> None:
        self.provider = provider or TushareMarketDataProvider()
        self.cache = cache or ReportPeriodCache()

    def prewarm(
        self,
        periods: list[str],
        interfaces: list[str] | None = None,
        *,
        mainbz_types: list[str] | None = None,
        force: bool = False,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        interfaces = interfaces or list(DEFAULT_INTERFACES)
        mainbz_types = mainbz_types or ["P", "I", "D"]
        jobs = []
        for period in periods:
            for interface in interfaces:
                dimensions = (
                    mainbz_types if interface == "mainbz"
                    else ["SSE", "SZSE", "BSE"] if interface == "stock_company"
                    else [None]
                )
                for dimension in dimensions:
                    params = {"period": period}
                    if interface == "mainbz":
                        params["type"] = dimension
                    elif interface == "stock_company":
                        params["exchange"] = dimension
                    jobs.append((interface, period, dimension, params))
        if dry_run:
            return {"dry_run": True, "job_count": len(jobs), "jobs": [item[3] | {"interface": item[0]} for item in jobs]}
        results = []
        for interface, period, dimension, params in jobs:
            def load(i=interface, p=period, d=dimension):
                if i == "stock_basic":
                    return self.provider.get_stock_basic_result(use_cache=False)
                if i == "stock_company":
                    return self.provider.get_stock_company(d, use_cache=False)
                return self.provider.get_fundamental_period_batch(i, p, mainbz_type=d, use_cache=False)
            payload = self.cache.fetch(
                interface,
                period,
                params,
                load,
                force=force,
            )
            results.append(payload["metadata"])
        return {
            "dry_run": False,
            "job_count": len(results),
            "results": results,
            "per_stock_api_call_count": self.provider.per_stock_api_call_count,
        }
