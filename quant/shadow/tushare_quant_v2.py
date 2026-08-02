from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from quant.normalizer import min_max_normalize, percentile_score


FACTOR_VERSION = "TUSHARE_QUANT_V2_CORRECTED_SHADOW"
WEIGHTS = {
    "technical": 0.25,
    "capital": 0.25,
    "emotion": 0.20,
    "momentum": 0.15,
    "risk": 0.15,
}


def code(value: Any) -> str:
    return str(value or "").split(".", 1)[0].zfill(6)


def q4(value: Any) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.0001")))


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def clip(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    return q4(max(low, min(high, finite(value))))


def linear(value: Any, low: float, high: float, *, reverse: bool = False) -> float:
    return float(min_max_normalize(value, low, high, higher_is_better=not reverse))


def percentile(values: Mapping[str, float]) -> dict[str, float]:
    universe = sorted(finite(value) for value in values.values())
    if not universe:
        return {}
    if universe[0] == universe[-1]:
        return {key: 50.0 for key in values}
    size = len(universe)
    return {
        key: q4(bisect_right(universe, finite(value)) / size * 100)
        for key, value in values.items()
    }


def moving_average(values: Sequence[float], size: int) -> float:
    return statistics.fmean(values[-size:]) if len(values) >= size else values[-1]


def return_n(closes: Sequence[float], size: int) -> float:
    if len(closes) <= size or closes[-size - 1] <= 0:
        return 0.0
    return closes[-1] / closes[-size - 1] - 1.0


def true_range_percent(history: Sequence[Mapping[str, Any]], size: int = 14) -> float:
    rows = history[-size:]
    if not rows:
        return 0.0
    ranges = []
    for row in rows:
        close = finite(row.get("close"))
        if close <= 0:
            continue
        ranges.append((finite(row.get("high"), close) - finite(row.get("low"), close)) / close)
    return statistics.fmean(ranges) if ranges else 0.0


def technical_score(history: Sequence[Mapping[str, Any]]) -> tuple[float, dict[str, float]]:
    closes = [finite(row.get("close")) for row in history if finite(row.get("close")) > 0]
    if len(closes) < 20:
        return 0.0, {
            "trend_structure": 0.0,
            "price_position": 0.0,
            "pattern_quality": 0.0,
            "volume_price_confirmation": 0.0,
            "volatility_adaptation": 0.0,
        }
    close = closes[-1]
    ma5, ma10, ma20 = (moving_average(closes, n) for n in (5, 10, 20))
    alignment = sum((close > ma5, ma5 > ma10, ma10 > ma20)) / 3 * 100
    slope = linear((ma5 / moving_average(closes[:-5], 5) - 1) * 100, -5, 8)
    trend = (alignment * 0.65) + (slope * 0.35)

    window = closes[-60:]
    low, high = min(window), max(window)
    position = (close - low) / (high - low) if high > low else 0.5
    if position <= 0.15:
        position_score = 25 + position / 0.15 * 25
    elif position <= 0.80:
        position_score = 50 + (position - 0.15) / 0.65 * 50
    else:
        position_score = 100 - (position - 0.80) / 0.20 * 65

    latest = history[-1]
    high20 = max(finite(row.get("high")) for row in history[-20:])
    breakout = linear(close / high20 if high20 else 0, 0.88, 1.0)
    day_low, day_high = finite(latest.get("low"), close), finite(latest.get("high"), close)
    close_location = (close - day_low) / (day_high - day_low) * 100 if day_high > day_low else 50
    pattern = breakout * 0.55 + close_location * 0.45

    volume_ratio = finite(latest.get("volume_ratio"), 1.0)
    pct = finite(latest.get("pct_chg"))
    volume_quality = linear(volume_ratio, 0.6, 2.0)
    direction = 75 if pct > 0 else 35 if pct < 0 else 50
    volume_confirmation = volume_quality * 0.55 + direction * 0.45

    atr = true_range_percent(history)
    volatility = 100.0 if 0.018 <= atr <= 0.055 else linear(atr, 0.005, 0.018) if atr < 0.018 else linear(atr, 0.12, 0.055)
    components = {
        "trend_structure": clip(trend),
        "price_position": clip(position_score),
        "pattern_quality": clip(pattern),
        "volume_price_confirmation": clip(volume_confirmation),
        "volatility_adaptation": clip(volatility),
    }
    score = (
        components["trend_structure"] * 8
        + components["price_position"] * 6
        + components["pattern_quality"] * 5
        + components["volume_price_confirmation"] * 4
        + components["volatility_adaptation"] * 2
    ) / 25
    return clip(score), components


def _capital_raw(
    daily: Mapping[str, Any],
    basic: Mapping[str, Any],
    flow: Mapping[str, Any] | None,
    ths_flow: Mapping[str, Any] | None,
) -> dict[str, float | None]:
    amount_cny = finite(daily.get("amount")) * 1000.0
    result: dict[str, float | None] = {
        "amount_cny": amount_cny,
        "volume_ratio": finite(basic.get("volume_ratio"), math.nan),
        "turnover_rate": finite(basic.get("turnover_rate"), math.nan),
        "main_inflow_rate": None,
        "ths_net_amount": None,
        "ths_net_d5_amount": None,
    }
    if flow:
        result["main_inflow_rate"] = (
            finite(flow.get("net_mf_amount")) * 10000.0 / amount_cny
            if amount_cny > 0
            else None
        )
    if ths_flow:
        result["ths_net_amount"] = finite(ths_flow.get("net_amount"))
        result["ths_net_d5_amount"] = finite(ths_flow.get("net_d5_amount"))
    return result


def capital_scores(
    codes: Sequence[str],
    daily_by_code: Mapping[str, Mapping[str, Any]],
    basic_by_code: Mapping[str, Mapping[str, Any]],
    flow_by_code: Mapping[str, Mapping[str, Any]],
    ths_flow_by_code: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    raw = {
        item: _capital_raw(
            daily_by_code[item],
            basic_by_code[item],
            flow_by_code.get(item),
            ths_flow_by_code.get(item),
        )
        for item in codes
        if item in daily_by_code and item in basic_by_code
    }
    rank_fields: dict[str, dict[str, float]] = {}
    for field in ("amount_cny", "main_inflow_rate", "ths_net_amount", "ths_net_d5_amount"):
        rank_fields[field] = percentile(
            {
                item: finite(values[field])
                for item, values in raw.items()
                if values[field] is not None and math.isfinite(finite(values[field], math.nan))
            }
        )
    scores: dict[str, float] = {}
    details: dict[str, dict[str, Any]] = {}
    for item in codes:
        values = raw.get(item)
        if not values:
            details[item] = {
                "score": 0.0,
                "capital_data_coverage": 0.0,
                "capital_confidence": "DATA_PIPELINE_ERROR",
                "missing_subfactors": ["ALL"],
                "direction_conflict": False,
            }
            scores[item] = 0.0
            continue
        sub: dict[str, float] = {
            "amount": rank_fields["amount_cny"][item],
            "volume_ratio": linear(values["volume_ratio"], 0.6, 2.5),
            "turnover": linear(values["turnover_rate"], 0.3, 8.0),
        }
        missing: list[str] = []
        for field, label in (
            ("main_inflow_rate", "main_inflow"),
            ("ths_net_amount", "ths_net_amount"),
            ("ths_net_d5_amount", "ths_net_d5_amount"),
        ):
            if item in rank_fields[field]:
                sub[label] = rank_fields[field][item]
            else:
                missing.append(label)
        direction_conflict = False
        if values["main_inflow_rate"] is not None and values["ths_net_amount"] is not None:
            direction_conflict = finite(values["main_inflow_rate"]) * finite(values["ths_net_amount"]) < 0
        coverage = len(sub) / 6
        confidence = "COMPLETE"
        if missing:
            confidence = "REWEIGHTED_NORMAL_MISSING"
        if direction_conflict:
            confidence = "DIRECTION_CONFLICT"
        score = statistics.fmean(sub.values())
        if direction_conflict:
            score = score * 0.9 + 50 * 0.1
        scores[item] = clip(score)
        details[item] = {
            **values,
            "subfactors": {key: q4(value) for key, value in sub.items()},
            "score": scores[item],
            "capital_data_coverage": q4(coverage),
            "capital_confidence": confidence,
            "missing_subfactors": missing,
            "direction_conflict": direction_conflict,
            "amount_raw": finite(daily_by_code[item].get("amount")),
            "amount_raw_unit": "THOUSAND_CNY",
            "amount_cny": finite(daily_by_code[item].get("amount")) * 1000.0,
            "unit_conversion_version": "TUSHARE_AMOUNT_THOUSAND_CNY_TO_CNY_V1",
        }
    return scores, details


def global_regime(
    daily_rows: Sequence[Mapping[str, Any]],
    limit_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    changes = [finite(row.get("pct_chg")) for row in daily_rows]
    advancing = sum(value > 0 for value in changes)
    declining = sum(value < 0 for value in changes)
    breadth = advancing / (advancing + declining) if advancing + declining else 0.5
    up = sum("涨停" in str(row.get("limit_type") or "") for row in limit_rows)
    down = sum("跌停" in str(row.get("limit_type") or "") for row in limit_rows)
    score = breadth * 70 + linear(up - down, -30, 80) * 0.30
    regime = "RISK_ON" if score >= 62 else "RISK_OFF" if score < 42 else "NEUTRAL"
    return {
        "regime": regime,
        "score": q4(score),
        "advancing_count": advancing,
        "declining_count": declining,
        "advancing_ratio": q4(breadth),
        "limit_up_count": up,
        "limit_down_count": down,
        "rank_contribution": 0.0,
    }


def emotion_scores(
    codes: Sequence[str],
    stock_by_code: Mapping[str, Mapping[str, Any]],
    daily_by_code: Mapping[str, Mapping[str, Any]],
    ths_sector_flow: Sequence[Mapping[str, Any]],
    limit_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    sectors: dict[str, list[str]] = defaultdict(list)
    for item in codes:
        sectors[str((stock_by_code.get(item) or {}).get("industry") or "UNKNOWN")].append(item)
    sector_return = {
        name: statistics.fmean(finite(daily_by_code[item].get("pct_chg")) for item in members)
        for name, members in sectors.items()
    }
    sector_breadth = {
        name: sum(finite(daily_by_code[item].get("pct_chg")) > 0 for item in members) / len(members) * 100
        for name, members in sectors.items()
    }
    return_rank = percentile(sector_return)
    flow_by_name = {str(row.get("name") or ""): row for row in ths_sector_flow}
    flow_rank = percentile(
        {
            name: finite(row.get("net_amount"))
            for name, row in flow_by_name.items()
        }
    )
    stock_return_rank = percentile(
        {item: finite(daily_by_code[item].get("pct_chg")) for item in codes}
    )
    local_return_rank: dict[str, float] = {}
    for members in sectors.values():
        local_return_rank.update(
            percentile(
                {
                    member: finite(daily_by_code[member].get("pct_chg"))
                    for member in members
                }
            )
        )
    limit_by_code = {code(row.get("ts_code")): row for row in limit_rows}
    scores: dict[str, float] = {}
    details: dict[str, dict[str, Any]] = {}
    for item in codes:
        industry = str((stock_by_code.get(item) or {}).get("industry") or "UNKNOWN")
        sector_flow_score = flow_rank.get(industry, 50.0)
        sector = (
            return_rank[industry] * 0.40
            + sector_breadth[industry] * 0.35
            + sector_flow_score * 0.25
        )
        limit_state = 50.0
        if item in limit_by_code:
            limit_type = str(limit_by_code[item].get("limit_type") or "")
            limit_state = 100.0 if "涨停" in limit_type else 0.0 if "跌停" in limit_type else 50.0
        stock = (
            stock_return_rank[item] * 0.55
            + local_return_rank[item] * 0.30
            + limit_state * 0.15
        )
        score = sector * 0.60 + stock * 0.40
        scores[item] = clip(score)
        details[item] = {
            "industry": industry,
            "sector_score": q4(sector),
            "stock_score": q4(stock),
            "sector_weight_points": 12,
            "stock_weight_points": 8,
            "global_emotion_rank_contribution": 0.0,
            "sector_flow_fallback": industry not in flow_by_name,
        }
    return scores, details


def momentum_scores(
    codes: Sequence[str],
    histories: Mapping[str, Sequence[Mapping[str, Any]]],
    stock_by_code: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    raw: dict[str, dict[str, float]] = {}
    for item in codes:
        closes = [finite(row.get("close")) for row in histories[item] if finite(row.get("close")) > 0]
        r5, r10, r20 = (return_n(closes, n) for n in (5, 10, 20))
        raw[item] = {"r5": r5, "r10": r10, "r20": r20, "acceleration": r5 - (r20 / 4)}
    ranks = {field: percentile({item: values[field] for item, values in raw.items()}) for field in raw[next(iter(raw))]}
    sector_means: dict[str, float] = {}
    sectors: dict[str, list[str]] = defaultdict(list)
    for item in codes:
        sectors[str((stock_by_code.get(item) or {}).get("industry") or "UNKNOWN")].append(item)
    for industry, members in sectors.items():
        sector_means[industry] = statistics.fmean(raw[item]["r5"] for item in members)
    relative = percentile(
        {
            item: raw[item]["r5"] - sector_means[str((stock_by_code.get(item) or {}).get("industry") or "UNKNOWN")]
            for item in codes
        }
    )
    scores: dict[str, float] = {}
    details: dict[str, dict[str, float]] = {}
    for item in codes:
        score = (
            ranks["r5"][item] * 0.30
            + ranks["r10"][item] * 0.20
            + ranks["r20"][item] * 0.15
            + ranks["acceleration"][item] * 0.20
            + relative[item] * 0.15
        )
        scores[item] = clip(score)
        details[item] = {**raw[item], "relative_sector_rank": relative[item]}
    return scores, details


def risk_scores(
    codes: Sequence[str],
    stock_by_code: Mapping[str, Mapping[str, Any]],
    daily_by_code: Mapping[str, Mapping[str, Any]],
    basic_by_code: Mapping[str, Mapping[str, Any]],
    histories: Mapping[str, Sequence[Mapping[str, Any]]],
    limit_by_code: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    amount_rank = percentile(
        {item: finite(daily_by_code[item].get("amount")) for item in codes}
    )
    scores: dict[str, float] = {}
    details: dict[str, dict[str, Any]] = {}
    for item in codes:
        meta = stock_by_code.get(item) or {}
        name = str(meta.get("name") or "")
        daily = daily_by_code[item]
        history = histories[item]
        hard_reasons: list[str] = []
        if "ST" in name.upper():
            hard_reasons.append("ST")
        if finite(daily.get("vol")) <= 0 or finite(daily.get("amount")) <= 0:
            hard_reasons.append("SUSPENDED_OR_UNTRADABLE")
        limit = limit_by_code.get(item) or {}
        if finite(limit.get("up_limit")) and finite(daily.get("close")) >= finite(limit.get("up_limit")) - 0.0001:
            hard_reasons.append("LIMIT_UP_NOT_BUYABLE")
        closes = [finite(row.get("close")) for row in history if finite(row.get("close")) > 0]
        peak = max(closes[-60:]) if closes else 0
        close = closes[-1] if closes else 0
        high_position = close / peak if peak else 0
        position_health = 100 if high_position <= 0.88 else linear(high_position, 1.05, 0.88)
        volatility_health = 100 - min(true_range_percent(history) * 500, 70)
        turnover = finite(basic_by_code[item].get("turnover_rate"))
        turnover_health = 100 if 0.5 <= turnover <= 12 else 45
        soft = amount_rank[item] * 0.35 + position_health * 0.30 + volatility_health * 0.20 + turnover_health * 0.15
        scores[item] = clip(soft)
        details[item] = {
            "hard_gate": bool(hard_reasons),
            "hard_gate_reasons": hard_reasons,
            "soft_risk_health": scores[item],
            "high_position_ratio": q4(high_position),
            "liquidity_rank": q4(amount_rank[item]),
            "turnover_rate": turnover,
        }
    return scores, details


def rank_rows(rows: Iterable[Mapping[str, Any]], version: str) -> list[dict[str, Any]]:
    ranked = sorted(
        (dict(row) for row in rows),
        key=lambda row: (-finite(row.get("total_score")), code(row.get("stock_code"))),
    )
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
        row["version"] = version
        row["factor_version"] = FACTOR_VERSION
    return ranked


def weighted_total(row: Mapping[str, Any]) -> float:
    return q4(sum(finite(row.get(f"{name}_score")) * weight for name, weight in WEIGHTS.items()))


def apply_concentration(
    ranked: Sequence[Mapping[str, Any]],
    stock_by_code: Mapping[str, Mapping[str, Any]],
    *,
    regime: str,
    target: int = 10,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    industry_counts: Counter[str] = Counter()
    direction_counts: Counter[str] = Counter()
    industry_cap = 2
    direction_cap = 1 if regime == "RISK_OFF" else 2
    for source in ranked:
        item = code(source.get("stock_code"))
        industry = str((stock_by_code.get(item) or {}).get("industry") or "UNKNOWN")
        direction = industry.split("-", 1)[0]
        reasons: list[str] = []
        if industry_counts[industry] >= industry_cap:
            reasons.append("INDUSTRY_MAX_2")
        if direction_counts[direction] >= direction_cap:
            reasons.append(f"DIRECTION_MAX_{direction_cap}")
        if source.get("hard_gate"):
            reasons.append("HARD_GATE")
        retained = not reasons and len(selected) < target
        if retained:
            selected.append(dict(source))
            industry_counts[industry] += 1
            direction_counts[direction] += 1
        decisions.append(
            {
                "stock_code": item,
                "industry": industry,
                "retained": retained,
                "reasons": reasons,
            }
        )
        if len(selected) >= target:
            break
    return selected, decisions


def compare_topn(
    legacy: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
    sizes: Sequence[int] = (20, 50, 100),
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for size in sizes:
        left = {code(row.get("stock_code")) for row in legacy[:size]}
        right = {code(row.get("stock_code")) for row in candidate[:size]}
        result[f"top{size}_overlap_count"] = len(left & right)
        result[f"top{size}_overlap_rate"] = q4(len(left & right) / size)
        result[f"top{size}_entered"] = sorted(right - left)
        result[f"top{size}_exited"] = sorted(left - right)
    return result


@dataclass(frozen=True)
class ValidationBuild:
    stages: dict[str, list[dict[str, Any]]]
    details: dict[str, dict[str, dict[str, Any]]]
    global_regime: dict[str, Any]


def build_validation(
    legacy_rows: Sequence[Mapping[str, Any]],
    *,
    histories: Mapping[str, Sequence[Mapping[str, Any]]],
    stock_by_code: Mapping[str, Mapping[str, Any]],
    daily_by_code: Mapping[str, Mapping[str, Any]],
    basic_by_code: Mapping[str, Mapping[str, Any]],
    flow_by_code: Mapping[str, Mapping[str, Any]],
    ths_flow_by_code: Mapping[str, Mapping[str, Any]],
    limit_by_code: Mapping[str, Mapping[str, Any]],
    limit_rows: Sequence[Mapping[str, Any]],
    ths_sector_flow: Sequence[Mapping[str, Any]],
    pro_factor_codes: set[str] | None = None,
) -> ValidationBuild:
    """Build the six immutable Shadow stages without changing Legacy inputs.

    ``stk_factor_pro`` is intentionally only a cross-check marker.  Every
    corrected technical value is derived from the point-in-time daily history.
    """

    eligible_codes = [
        code(row.get("stock_code"))
        for row in legacy_rows
        if code(row.get("stock_code")) in daily_by_code
        and code(row.get("stock_code")) in basic_by_code
        and len(histories.get(code(row.get("stock_code")), ())) >= 20
    ]
    legacy_by_code = {code(row.get("stock_code")): dict(row) for row in legacy_rows}
    pro_codes = pro_factor_codes or set()

    technical: dict[str, float] = {}
    technical_detail: dict[str, dict[str, Any]] = {}
    for item in eligible_codes:
        score, detail = technical_score(histories[item])
        has_pro = item in pro_codes
        technical[item] = score
        technical_detail[item] = {
            **detail,
            "technical_data_source": "LOCAL_DERIVED",
            "technical_crosscheck_source": "TUSHARE" if has_pro else None,
            "technical_fallback_status": "PRO_CROSSCHECK_AVAILABLE"
            if has_pro
            else "PRO_FACTOR_MISSING",
            "technical_confidence": "HIGH" if has_pro else "DEGRADED",
            "history_length": len(histories[item]),
        }

    capital, capital_detail = capital_scores(
        eligible_codes,
        daily_by_code,
        basic_by_code,
        flow_by_code,
        ths_flow_by_code,
    )
    emotion, emotion_detail = emotion_scores(
        eligible_codes,
        stock_by_code,
        daily_by_code,
        ths_sector_flow,
        limit_rows,
    )
    momentum, momentum_detail = momentum_scores(
        eligible_codes,
        histories,
        stock_by_code,
    )
    risk, risk_detail = risk_scores(
        eligible_codes,
        stock_by_code,
        daily_by_code,
        basic_by_code,
        histories,
        limit_by_code,
    )
    regime = global_regime(list(daily_by_code.values()), limit_rows)

    def make_stage(
        version: str,
        *,
        use_technical: bool,
        use_capital: bool,
        use_emotion: bool,
        use_momentum: bool,
        use_risk: bool,
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for item in eligible_codes:
            source = dict(legacy_by_code[item])
            row = {
                **source,
                "stock_code": item,
                "technical_score": technical[item]
                if use_technical
                else finite(source.get("technical_score")),
                "capital_score": capital[item]
                if use_capital
                else finite(source.get("capital_score")),
                "emotion_score": emotion[item]
                if use_emotion
                else finite(source.get("emotion_score")),
                "momentum_score": momentum[item]
                if use_momentum
                else finite(source.get("momentum_score")),
                "risk_score": risk[item]
                if use_risk
                else finite(source.get("risk_score")),
                "hard_gate": risk_detail[item]["hard_gate"] if use_risk else False,
                "hard_gate_reasons": risk_detail[item]["hard_gate_reasons"]
                if use_risk
                else [],
            }
            row["total_score"] = weighted_total(row)
            output.append(row)
        return rank_rows(output, version)

    stages = {
        "S0_LEGACY": rank_rows(
            (
                {
                    **dict(legacy_by_code[item]),
                    "stock_code": item,
                    "hard_gate": False,
                    "hard_gate_reasons": [],
                }
                for item in eligible_codes
            ),
            "S0_LEGACY",
        ),
        "CORRECTED_CORE_V2": make_stage(
            "CORRECTED_CORE_V2",
            use_technical=True,
            use_capital=False,
            use_emotion=False,
            use_momentum=True,
            use_risk=False,
        ),
        "CAPITAL_V2": make_stage(
            "CAPITAL_V2",
            use_technical=True,
            use_capital=True,
            use_emotion=False,
            use_momentum=True,
            use_risk=False,
        ),
        "SECTOR_STOCK_EMOTION_V1": make_stage(
            "SECTOR_STOCK_EMOTION_V1",
            use_technical=True,
            use_capital=True,
            use_emotion=True,
            use_momentum=True,
            use_risk=False,
        ),
        "RISK_V2": make_stage(
            "RISK_V2",
            use_technical=True,
            use_capital=True,
            use_emotion=True,
            use_momentum=True,
            use_risk=True,
        ),
    }
    stages[FACTOR_VERSION] = [
        {
            **row,
            "technical_data_source": technical_detail[row["stock_code"]][
                "technical_data_source"
            ],
            "technical_confidence": technical_detail[row["stock_code"]][
                "technical_confidence"
            ],
            "technical_fallback_status": technical_detail[row["stock_code"]][
                "technical_fallback_status"
            ],
            "sector_data_source": "TUSHARE",
            "concept_data_source": "TUSHARE",
            "capital_confidence": capital_detail[row["stock_code"]][
                "capital_confidence"
            ],
            "capital_data_coverage": capital_detail[row["stock_code"]][
                "capital_data_coverage"
            ],
        }
        for row in stages["RISK_V2"]
    ]
    for index, row in enumerate(stages[FACTOR_VERSION], 1):
        row["rank"] = index
        row["version"] = FACTOR_VERSION

    return ValidationBuild(
        stages=stages,
        details={
            "technical": technical_detail,
            "capital": capital_detail,
            "emotion": emotion_detail,
            "momentum": momentum_detail,
            "risk": risk_detail,
        },
        global_regime=regime,
    )
