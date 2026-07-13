from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import prod
from typing import Any

from review.performance_schemas import MarketBar, MemberSnapshot


def compound_return(values: list[float]) -> float:
    return prod(1.0 + value for value in values) - 1.0


def drawdown(value: float, historical_peak: float) -> float:
    return value / historical_peak - 1.0 if historical_peak else 0.0


class ReturnCalculator:
    def calculate(
        self,
        member: MemberSnapshot,
        bars: dict[date, MarketBar],
        evaluation_dates: list[date],
        return_basis: str,
    ) -> list[dict[str, Any]]:
        selection_bar = bars.get(member.selection_trade_date)
        baseline_date = member.selection_trade_date if return_basis == "SIGNAL_CLOSE" else (evaluation_dates[0] if evaluation_dates else None)
        baseline_bar = bars.get(baseline_date) if baseline_date else None
        baseline_price = (
            baseline_bar.close if return_basis == "SIGNAL_CLOSE" and baseline_bar else
            baseline_bar.open if return_basis == "NEXT_OPEN" and baseline_bar else None
        )
        valid_returns: list[float] = []
        previous_valid_close = baseline_price
        peak_value = 1.0
        max_drawdown = 0.0
        rows: list[dict[str, Any]] = []

        for holding_day, trade_date in enumerate(evaluation_dates, start=1):
            bar = bars.get(trade_date)
            daily: float | None = None
            source = "MISSING"
            status = "UNKNOWN_MISSING"
            if bar and bar.suspended:
                daily, source, status = 0.0, "CARRIED_FORWARD", "SUSPENDED_CARRY_FORWARD"
            elif bar and bar.close is not None and baseline_price is not None:
                if holding_day == 1:
                    denominator = baseline_price
                    daily = bar.close / denominator - 1.0
                    source = "NEXT_OPEN_CLOSE" if return_basis == "NEXT_OPEN" else "CLOSE_PRE_CLOSE"
                elif bar.pct_chg is not None:
                    daily = bar.pct_chg / 100.0
                    source = "PCT_CHG"
                elif previous_valid_close:
                    daily = bar.close / previous_valid_close - 1.0
                    source = "CLOSE_PRE_CLOSE"
                status = "AVAILABLE"
            elif not evaluation_dates:
                status = "PENDING_FIRST_EVALUATION"

            if daily is not None:
                valid_returns.append(daily)
                if bar and bar.close is not None:
                    previous_valid_close = bar.close
            cumulative = compound_return(valid_returns) if valid_returns else None
            if cumulative is not None:
                current_value = 1.0 + cumulative
                peak_value = max(peak_value, current_value)
                current_drawdown = drawdown(current_value, peak_value)
                max_drawdown = min(max_drawdown, current_drawdown)
            else:
                current_drawdown = None
            rows.append({
                "cohort_member_id": member.member_id,
                "evaluation_trade_date": trade_date,
                "holding_day": holding_day,
                "baseline_trade_date": baseline_date,
                "baseline_price": baseline_price,
                "open_price": bar.open if bar else None,
                "high_price": bar.high if bar else None,
                "low_price": bar.low if bar else None,
                "close_price": bar.close if bar else None,
                "previous_close": bar.pre_close if bar else previous_valid_close,
                "daily_return": daily,
                "cumulative_return": cumulative,
                "peak_cumulative_return": peak_value - 1.0 if cumulative is not None else None,
                "drawdown_to_date": current_drawdown,
                "max_drawdown_to_date": max_drawdown if cumulative is not None else None,
                "return_source": source,
                "data_status": status,
            })
        return rows


class PortfolioReturnCalculator:
    def calculate(
        self,
        cohort_id: int,
        members: list[MemberSnapshot],
        stock_rows: list[dict[str, Any]],
        weighting_mode: str,
    ) -> list[dict[str, Any]]:
        member_map = {member.member_id: member for member in members}
        by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for row in stock_rows:
            by_date[row["evaluation_trade_date"]].append(row)
        portfolio_returns: list[float] = []
        peak_value = 1.0
        max_drawdown = 0.0
        result = []
        for trade_date in sorted(by_date):
            rows = by_date[trade_date]
            valid = [row for row in rows if row["daily_return"] is not None]
            suspended = sum(row["data_status"] == "SUSPENDED_CARRY_FORWARD" for row in rows)
            missing = len(rows) - len(valid)
            daily_return: float | None = None
            status = "SUCCESS"
            if weighting_mode == "SUGGESTED_POSITION_WEIGHT":
                weighted = [(row, max(0.0, member_map[row["cohort_member_id"]].suggested_position_percent)) for row in valid]
                weight_total = sum(weight for _, weight in weighted)
                if weight_total > 0:
                    daily_return = sum(row["daily_return"] * weight / weight_total for row, weight in weighted)
                else:
                    status = "POSITION_WEIGHT_UNAVAILABLE"
            elif valid:
                daily_return = sum(row["daily_return"] for row in valid) / len(valid)
            else:
                status = "NO_VALID_MARKET_DATA"
            if daily_return is not None:
                portfolio_returns.append(daily_return)
            cumulative = compound_return(portfolio_returns) if portfolio_returns else None
            if cumulative is not None:
                value = 1.0 + cumulative
                peak_value = max(peak_value, value)
                current_drawdown = drawdown(value, peak_value)
                max_drawdown = min(max_drawdown, current_drawdown)
            else:
                current_drawdown = None
            coverage = len(valid) / len(rows) if rows else 0.0
            if status == "SUCCESS" and coverage < 1.0:
                status = "PARTIAL_SUCCESS"
            best = max(valid, key=lambda row: row["daily_return"], default=None)
            worst = min(valid, key=lambda row: row["daily_return"], default=None)
            positives = sum(row["daily_return"] > 0 for row in valid)
            result.append({
                "cohort_id": cohort_id,
                "evaluation_trade_date": trade_date,
                "holding_day": min(row["holding_day"] for row in rows),
                "weighting_mode": weighting_mode,
                "total_member_count": len(rows),
                "valid_member_count": len(valid),
                "suspended_count": suspended,
                "missing_count": missing,
                "daily_return": daily_return,
                "cumulative_return": cumulative,
                "win_rate": positives / len(valid) if valid else None,
                "drawdown_to_date": current_drawdown,
                "max_drawdown_to_date": max_drawdown if cumulative is not None else None,
                "coverage_ratio": coverage,
                "best_stock_code": member_map[best["cohort_member_id"]].stock_code if best else None,
                "worst_stock_code": member_map[worst["cohort_member_id"]].stock_code if worst else None,
                "status": status,
            })
        return result
