from __future__ import annotations

import statistics
from typing import Any


class MarketAdjustedPerformanceEvaluator:
    """Evaluate absolute and benchmark-relative outcomes without replacing either view."""

    @staticmethod
    def evaluate(stock_daily: list[float], benchmark_daily: list[float] | None, *, benchmark_type: str | None, benchmark_code: str | None, benchmark_quality: str, missing_reason: str | None = None) -> dict[str, Any]:
        stock_curve=_curve(stock_daily)
        benchmark_curve=_curve(benchmark_daily or []) if benchmark_daily is not None else []
        output={"benchmark_type":benchmark_type,"benchmark_code":benchmark_code,"benchmark_quality":benchmark_quality,"benchmark_missing_reason":missing_reason}
        for horizon in (1,3,5):
            stock=stock_curve[horizon-1] if len(stock_curve)>=horizon else None
            benchmark=benchmark_curve[horizon-1] if len(benchmark_curve)>=horizon else None
            output[f"stock_return_D{horizon}"]=stock;output[f"benchmark_return_D{horizon}"]=benchmark
            output[f"alpha_D{horizon}"]=stock-benchmark if stock is not None and benchmark is not None else None
        alpha_daily=[stock-benchmark for stock,benchmark in zip(stock_daily,benchmark_daily or [])]
        alpha_curve=_curve(alpha_daily)
        output["industry_alpha"]=alpha_curve[-1] if alpha_curve and benchmark_type=="INDUSTRY" else None
        output["broad_market_alpha"]=alpha_curve[-1] if alpha_curve and benchmark_type=="BROAD_MARKET" else None
        output["alpha_MAE"]=min([0.0,*alpha_curve]) if alpha_curve else None
        output["alpha_MFE"]=max([0.0,*alpha_curve]) if alpha_curve else None
        output["cumulative_alpha"]=alpha_curve[-1] if alpha_curve else None
        return output

    @staticmethod
    def metrics(rows: list[dict[str,Any]], *, selected_key: str = "selected") -> dict[str,Any]:
        selected=[row for row in rows if row.get(selected_key)]
        absolute=[row.get("cumulative_return") for row in selected if row.get("cumulative_return") is not None]
        alpha=[row.get("cumulative_alpha") for row in selected if row.get("cumulative_alpha") is not None]
        industry_alpha=[row.get("cumulative_alpha") for row in selected if row.get("benchmark_type")=="INDUSTRY" and row.get("cumulative_alpha") is not None]
        broad_alpha=[row.get("cumulative_alpha") for row in selected if row.get("benchmark_type")=="BROAD_MARKET" and row.get("cumulative_alpha") is not None]
        removed=[row for row in rows if not row.get(selected_key) and row.get("cumulative_return") is not None]
        wins=[v for v in alpha if v>0];losses=[v for v in alpha if v<0]
        avoided=[row for row in removed if row["cumulative_return"]<=0];missed=[row for row in removed if row["cumulative_return"]>0]
        return {
            "count":len(selected),"absolute_observed":len(absolute),"relative_observed":len(alpha),
            "absolute_win_rate":sum(v>0 for v in absolute)/len(absolute) if absolute else None,
            "relative_win_rate":sum(v>0 for v in alpha)/len(alpha) if alpha else None,
            "industry_relative_win_rate":sum(v>0 for v in industry_alpha)/len(industry_alpha) if industry_alpha else None,
            "broad_market_relative_win_rate":sum(v>0 for v in broad_alpha)/len(broad_alpha) if broad_alpha else None,
            "average_return":statistics.fmean(absolute) if absolute else None,"average_alpha":statistics.fmean(alpha) if alpha else None,
            "median_alpha":statistics.median(alpha) if alpha else None,
            "alpha_profit_factor":sum(wins)/abs(sum(losses)) if wins and losses else None,
            "alpha_MAE":statistics.fmean(row["alpha_MAE"] for row in selected if row.get("alpha_MAE") is not None) if any(row.get("alpha_MAE") is not None for row in selected) else None,
            "alpha_MFE":statistics.fmean(row["alpha_MFE"] for row in selected if row.get("alpha_MFE") is not None) if any(row.get("alpha_MFE") is not None for row in selected) else None,
            "target_before_stop_rate":None,
            "risk_avoidance_rate":len(avoided)/len(removed) if removed else None,
            "no_trade_quality":len(avoided)/len(removed) if removed else None,
            "opportunity_cost":sum(row["cumulative_return"] for row in missed),
            "missed_winners":len(missed),"avoided_losers":len(avoided),
            "maximum_loss":min(absolute) if absolute else None,
            "tail_losses":{str(t):sum(v<t for v in absolute) for t in (-.05,-.10,-.15,-.20)},
        }


def _curve(values: list[float]) -> list[float]:
    wealth=1.0; output=[]
    for value in values:
        wealth*=1+float(value);output.append(wealth-1)
    return output
