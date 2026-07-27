from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from scipy.stats import spearmanr


EXECUTION_POLICY = "NEXT_OPEN"
PROMOTION_CONCLUSION = "KEEP_LEGACY_AND_SHADOW"


def profit_factor(values: Iterable[float]) -> float | None:
    returns = list(values)
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    if losses:
        return gains / losses
    return None if gains == 0 else math.inf


def maximum_drawdown(values: Iterable[float]) -> float | None:
    returns = list(values)
    if not returns:
        return None
    equity = peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def validate_same_execution_basis(rows: Sequence[Mapping[str, Any]]) -> None:
    policies = {str(row.get("execution_policy")) for row in rows}
    if policies - {EXECUTION_POLICY}:
        raise ValueError(f"FORWARD_EXECUTION_POLICY_MISMATCH:{sorted(policies)}")
    by_key: dict[tuple[str, str], set[tuple[Any, ...]]] = defaultdict(set)
    for row in rows:
        key = (str(row["trade_date"]), str(row["stock_code"]))
        by_key[key].add(
            (
                row.get("entry_trade_date"),
                row.get("entry_price"),
                row.get("entry_status"),
            )
        )
    conflicts = [key for key, values in by_key.items() if len(values) > 1]
    if conflicts:
        raise ValueError(f"FORWARD_EXECUTION_BASIS_CONFLICT:{conflicts[0]}")


def evaluate_forward_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    top_sizes: Sequence[int] = (20, 50, 100),
) -> list[dict[str, Any]]:
    validate_same_execution_basis(rows)
    output: list[dict[str, Any]] = []
    by_version_date: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_version_date[(str(row["version_key"]), str(row["trade_date"]))].append(row)
    for (version, trade_date), group in sorted(by_version_date.items()):
        ranked = sorted(group, key=lambda item: int(item["rank"]))
        baseline = sorted(
            by_version_date.get(("S0_LEGACY", trade_date), []),
            key=lambda item: int(item["rank"]),
        )
        for top_n in top_sizes:
            selected = [
                row
                for row in ranked[:top_n]
                if row.get("entry_status") == "FILLED"
            ]
            for horizon in ("d1", "d3", "d5"):
                values = [
                    float(row[f"return_{horizon}"])
                    for row in selected
                    if row.get(f"return_{horizon}") is not None
                ]
                ranks = [
                    float(row["rank"])
                    for row in selected
                    if row.get(f"return_{horizon}") is not None
                ]
                ic = (
                    float(spearmanr([-value for value in ranks], values).statistic)
                    if len(values) >= 3 and len(set(values)) > 1
                    else None
                )
                bottom_values = [
                    float(row[f"return_{horizon}"])
                    for row in ranked[-top_n:]
                    if row.get("entry_status") == "FILLED"
                    and row.get(f"return_{horizon}") is not None
                ]
                top_bottom_spread = (
                    statistics.fmean(values) - statistics.fmean(bottom_values)
                    if values and bottom_values
                    else None
                )
                baseline_top = {
                    str(row["stock_code"]): row for row in baseline[:top_n]
                }
                candidate_top = {
                    str(row["stock_code"]): row for row in ranked[:top_n]
                }
                entered = set(candidate_top) - set(baseline_top)
                exited = set(baseline_top) - set(candidate_top)
                entered_returns = [
                    float(candidate_top[code][f"return_{horizon}"])
                    for code in entered
                    if candidate_top[code].get("entry_status") == "FILLED"
                    and candidate_top[code].get(f"return_{horizon}") is not None
                ]
                exited_returns = [
                    float(baseline_top[code][f"return_{horizon}"])
                    for code in exited
                    if baseline_top[code].get("entry_status") == "FILLED"
                    and baseline_top[code].get(f"return_{horizon}") is not None
                ]
                incremental = (
                    statistics.fmean(entered_returns)
                    - statistics.fmean(exited_returns)
                    if entered_returns and exited_returns
                    else None
                )
                full_a_excess = [
                    float(row["full_a_equal_weight_excess"])
                    for row in selected
                    if row.get("full_a_equal_weight_excess") is not None
                ]
                industry_excess = [
                    float(row["industry_equal_weight_excess"])
                    for row in selected
                    if row.get("industry_equal_weight_excess") is not None
                ]
                output.append(
                    {
                        "version_key": version,
                        "trade_date": trade_date,
                        "top_n": top_n,
                        "horizon": horizon.upper(),
                        "sample_count": len(values),
                        "average_return": statistics.fmean(values) if values else None,
                        "win_rate": sum(value > 0 for value in values) / len(values)
                        if values
                        else None,
                        "profit_factor": profit_factor(values),
                        "rank_ic": ic,
                        "top_bottom_spread": top_bottom_spread,
                        "maximum_drawdown": maximum_drawdown(values),
                        "incremental_replacement_return": incremental,
                        "replacement_entered_count": len(entered),
                        "replacement_exited_count": len(exited),
                        "full_a_equal_weight_excess": statistics.fmean(full_a_excess)
                        if full_a_excess
                        else None,
                        "industry_equal_weight_excess": statistics.fmean(
                            industry_excess
                        )
                        if industry_excess
                        else None,
                        "average_mfe": statistics.fmean(
                            float(row["mfe"])
                            for row in selected
                            if row.get("mfe") is not None
                        )
                        if any(row.get("mfe") is not None for row in selected)
                        else None,
                        "average_mae": statistics.fmean(
                            float(row["mae"])
                            for row in selected
                            if row.get("mae") is not None
                        )
                        if any(row.get("mae") is not None for row in selected)
                        else None,
                        "execution_policy": EXECUTION_POLICY,
                    }
                )
    return output


def promotion_status(
    *,
    trading_days: int,
    complete_d3_tradable_samples: int,
    timing_contract_failures: int,
    data_coverage_explainable: bool,
    stable_vs_s0: bool,
) -> str:
    eligible = (
        trading_days >= 20
        and complete_d3_tradable_samples >= 100
        and timing_contract_failures == 0
        and data_coverage_explainable
        and stable_vs_s0
    )
    return "ELIGIBLE_FOR_MANUAL_PROMOTION_REVIEW" if eligible else PROMOTION_CONCLUSION
