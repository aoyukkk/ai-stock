from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from statistics import median, pstdev
from typing import Any


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def validate_radar_config(config: dict[str, Any]) -> None:
    for key in ("weights", "relative_strength_weights", "volume_price_weights", "sector_weights"):
        values = config.get(key) or {}
        if not values or abs(sum(float(value) for value in values.values()) - 1.0) > 1e-9:
            raise ValueError(f"MIDDAY_RADAR_WEIGHT_SUM_INVALID:{key}")
    lower = float(config["winsor_lower_quantile"])
    upper = float(config["winsor_upper_quantile"])
    if not 0 <= lower < upper <= 1:
        raise ValueError("MIDDAY_RADAR_WINSOR_RANGE_INVALID")


def percentile_scores(values: dict[str, float | None], lower: float = 0.01, upper: float = 0.99) -> dict[str, dict[str, float | None]]:
    valid = sorted(float(value) for value in values.values() if value is not None and math.isfinite(float(value)))
    if not valid:
        return {key: {"raw_value": value, "winsorized_value": None, "percentile": None, "normalized_value": None} for key, value in values.items()}
    low = _quantile(valid, lower)
    high = _quantile(valid, upper)
    clipped = {key: min(high, max(low, float(value))) for key, value in values.items() if value is not None and math.isfinite(float(value))}
    ordered = sorted((value, key) for key, value in clipped.items())
    rank: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        pct = 50.0 if len(ordered) == 1 else ((index + end - 1) / 2) / (len(ordered) - 1) * 100
        for _, key in ordered[index:end]:
            rank[key] = pct
        index = end
    return {
        key: {
            "raw_value": value,
            "winsorized_value": clipped.get(key),
            "percentile": rank.get(key),
            "normalized_value": rank.get(key),
        }
        for key, value in values.items()
    }


def weighted_available(values: dict[str, float | None], weights: dict[str, float]) -> tuple[float | None, dict[str, float]]:
    available = {key: float(value) for key, value in values.items() if value is not None and key in weights}
    denominator = sum(float(weights[key]) for key in available)
    if not available or denominator <= 0:
        return None, {}
    effective = {key: float(weights[key]) / denominator for key in available}
    return sum(available[key] * effective[key] for key in available), effective


def full_a_breadth(rows: list[dict[str, Any]], expected_count: int, minimum_coverage: float) -> dict[str, Any]:
    returns = [float(row["change_pct_to_cutoff"]) for row in rows if row.get("change_pct_to_cutoff") is not None]
    coverage = len(returns) / expected_count if expected_count else 0.0
    industries = industry_state(rows)
    return {
        "scope": "FULL_A_MARKET_BREADTH" if coverage >= minimum_coverage else "PARTIAL_MARKET_BREADTH",
        "expected_count": expected_count, "valid_count": len(returns), "coverage": coverage,
        "positive_count": sum(value > 0 for value in returns), "negative_count": sum(value < 0 for value in returns),
        "flat_count": sum(value == 0 for value in returns),
        "positive_ratio": sum(value > 0 for value in returns) / len(returns) if returns else None,
        "negative_ratio": sum(value < 0 for value in returns) / len(returns) if returns else None,
        "advancing_ratio": sum(value > 0 for value in returns) / len(returns) if returns else None,
        "declining_ratio": sum(value < 0 for value in returns) / len(returns) if returns else None,
        "median_return": median(returns) if returns else None,
        "equal_weight_return": sum(returns) / len(returns) if returns else None,
        "gain_above_3_count": sum(value >= .03 for value in returns), "gain_above_5_count": sum(value >= .05 for value in returns),
        "loss_below_3_count": sum(value <= -.03 for value in returns), "loss_below_5_count": sum(value <= -.05 for value in returns),
        "below_3_count": sum(value <= -.03 for value in returns), "below_5_count": sum(value <= -.05 for value in returns),
        "limit_up_count": sum(bool(row.get("at_limit_up")) for row in rows), "limit_down_count": sum(bool(row.get("at_limit_down")) for row in rows),
        "cross_section_volatility": pstdev(returns) if len(returns) > 1 else None,
        "industry_positive_ratio": sum(item["industry_return_equal_weight"] > 0 for item in industries) / len(industries) if industries else None,
        "industry_median_return": median([item["industry_return_equal_weight"] for item in industries]) if industries else None,
    }


def industry_state(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        industry = str(row.get("industry") or "UNKNOWN")
        if industry != "UNKNOWN":
            grouped[industry].append(row)
    output = []
    for industry, members in grouped.items():
        valid = [row for row in members if row.get("change_pct_to_cutoff") is not None]
        returns = [float(row["change_pct_to_cutoff"]) for row in valid]
        amount_ratios = [float(row["morning_amount_ratio"]) for row in valid if row.get("morning_amount_ratio") is not None]
        output.append({
            "sector_name": industry, "industry_stock_count": len(members), "industry_valid_count": len(valid),
            "industry_return_equal_weight": sum(returns) / len(returns) if returns else 0.0,
            "industry_median_return": median(returns) if returns else 0.0,
            "industry_positive_ratio": sum(value > 0 for value in returns) / len(returns) if returns else 0.0,
            "industry_turnover_strength": median(amount_ratios) if amount_ratios else None,
            "industry_top_stock_return": max(returns) if returns else None,
            "industry_dispersion": pstdev(returns) if len(returns) > 1 else 0.0,
            "classification_standard": "TUSHARE_STOCK_BASIC_INDUSTRY",
        })
    output.sort(key=lambda row: (-row["industry_return_equal_weight"], row["sector_name"]))
    for rank, row in enumerate(output, 1):
        row["industry_rank"] = rank
        row["industry_strength_score"] = 50.0 if len(output) == 1 else (len(output) - rank) / (len(output) - 1) * 100
        row["change_percent"] = row["industry_return_equal_weight"]
        row["positive_ratio"] = row["industry_positive_ratio"]
        row["strength"] = row["industry_strength_score"] / 100
    return output


def score_full_a(rows: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    validate_radar_config(config)
    lower = float(config["winsor_lower_quantile"]); upper = float(config["winsor_upper_quantile"])
    industries = industry_state(rows); industry_by = {row["sector_name"]: row for row in industries}
    market_median = median([float(row["change_pct_to_cutoff"]) for row in rows if row.get("change_pct_to_cutoff") is not None])
    raw = {
        "return": {row["stock_code"]: row.get("change_pct_to_cutoff") for row in rows},
        "industry_alpha": {row["stock_code"]: _difference(row.get("change_pct_to_cutoff"), (industry_by.get(row.get("industry")) or {}).get("industry_median_return")) for row in rows},
        "market_alpha": {row["stock_code"]: _difference(row.get("change_pct_to_cutoff"), market_median) for row in rows},
        "amount_ratio": {row["stock_code"]: row.get("morning_amount_ratio") for row in rows},
        "turnover": {row["stock_code"]: row.get("turnover_rate_to_cutoff") for row in rows},
    }
    normalized = {key: percentile_scores(value, lower, upper) for key, value in raw.items()}
    within: dict[str, dict[str, float]] = {}
    for industry in industries:
        members = {row["stock_code"]: row.get("change_pct_to_cutoff") for row in rows if row.get("industry") == industry["sector_name"]}
        within[industry["sector_name"]] = {key: value["normalized_value"] for key, value in percentile_scores(members, lower, upper).items()}
    scored=[]
    for row in rows:
        code=row["stock_code"]; industry=industry_by.get(row.get("industry"), {})
        close_location=_close_location(row); amplitude=float(row.get("amplitude_to_cutoff") or 0)
        trend_efficiency=_clamp(50 + 50 * float(row.get("change_pct_to_cutoff") or 0) / max(amplitude, .005))
        amount_pct=normalized["amount_ratio"][code]["normalized_value"]
        consistency=_price_volume_consistency(row.get("change_pct_to_cutoff"), amount_pct, row.get("high_to_close_drawdown"))
        relative,effective_relative=weighted_available({
            "stock_return_percentile":normalized["return"][code]["normalized_value"],
            "industry_alpha_percentile":normalized["industry_alpha"][code]["normalized_value"],
            "market_alpha_percentile":normalized["market_alpha"][code]["normalized_value"],
            "morning_close_location":close_location,
        },config["relative_strength_weights"])
        volume_price,effective_volume=weighted_available({
            "morning_amount_ratio":amount_pct,
            "turnover_strength":normalized["turnover"][code]["normalized_value"],
            "price_volume_consistency":consistency,
            "morning_trend_efficiency":trend_efficiency,
        },config["volume_price_weights"])
        sector,effective_sector=weighted_available({
            "industry_return_rank":industry.get("industry_strength_score"),
            "industry_breadth":float(industry.get("industry_positive_ratio",0))*100 if industry else None,
            "stock_rank_within_industry":within.get(row.get("industry"),{}).get(code),
        },config["sector_weights"])
        opening=_opening_risk_quality(row)
        components={"baseline_quant":float(row["baseline_quant_score"]),"morning_relative_strength":relative,"morning_volume_price":volume_price,"sector_resonance":sector,"opening_risk_quality":opening}
        radar,effective=weighted_available(components,config["weights"])
        if row.get("hard_block"):
            radar=min(float(radar or 0),float(row.get("hard_block_score_cap",0)))
        detail={"components":components,"effective_weights":effective,"relative_effective_weights":effective_relative,"volume_effective_weights":effective_volume,"sector_effective_weights":effective_sector,"normalized":{key:normalized[key][code] for key in normalized}}
        scored.append({**row,"morning_relative_strength":relative,"morning_volume_price":volume_price,"sector_resonance":sector,"opening_risk_quality":opening,"midday_radar_score":float(radar or 0),"score_detail":detail,"score_version":config["version"]})
    scored.sort(key=lambda row:(-row["midday_radar_score"],-(row.get("morning_relative_strength") or -1),-(row.get("sector_resonance") or -1),-row["baseline_quant_score"],-(row.get("opening_risk_quality") or -1),row["stock_code"]))
    industry_counts:dict[str,int]=defaultdict(int)
    for rank,row in enumerate(scored,1):
        row["midday_rank"]=rank;industry_counts[row.get("industry") or "UNKNOWN"]+=1;row["industry_rank"]=industry_counts[row.get("industry") or "UNKNOWN"]
    return scored,industries


def _opening_risk_quality(row: dict[str, Any]) -> float:
    gap=abs(float(row.get("opening_gap_pct") or 0)); amplitude=float(row.get("amplitude_to_cutoff") or 0); drawdown=abs(float(row.get("high_to_close_drawdown") or 0)); change=float(row.get("change_pct_to_cutoff") or 0); amount_ratio=float(row.get("morning_amount_ratio") or 0)
    penalty=min(30,gap*500)+min(20,amplitude*150)+min(25,drawdown*400)
    if change>.07:penalty+=15
    if amount_ratio>2 and change<.01:penalty+=15
    if row.get("data_quality") not in {"VALID_EXACT","VALID_RECONSTRUCTED"}:penalty+=30
    return _clamp(100-penalty)


def _price_volume_consistency(change: Any, amount_percentile: Any, drawdown: Any) -> float | None:
    if change is None or amount_percentile is None:return None
    score=50+(float(amount_percentile)-50)*(1 if float(change)>=0 else -1)
    score-=min(30,abs(float(drawdown or 0))*400)
    return _clamp(score)


def _close_location(row: dict[str, Any]) -> float | None:
    high=row.get("session_high");low=row.get("session_low");close=row.get("close_at_cutoff")
    if None in (high,low,close):return None
    return 50.0 if float(high)==float(low) else _clamp((float(close)-float(low))/(float(high)-float(low))*100)


def _difference(left: Any,right: Any)->float|None:
    return None if left is None or right is None else float(left)-float(right)


def _quantile(values:list[float],q:float)->float:
    if len(values)==1:return values[0]
    position=(len(values)-1)*q;low=int(position);high=min(low+1,len(values)-1);fraction=position-low
    return values[low]*(1-fraction)+values[high]*fraction


def _clamp(value:float)->float:return min(100.0,max(0.0,float(value)))
