from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from quant.normalizer import min_max_normalize


VERSION_KEY = "S2_1_MISSINGNESS_POLICY"
FACTOR_VERSION = "shadow_quant_v2_1::S2_1_MISSINGNESS_POLICY"
TRADE_KEY = "20260722"
AMOUNT_CONVERSION_VERSION = "TUSHARE_AMOUNT_THOUSAND_CNY_TO_CNY_V1"


class DataPipelineError(RuntimeError):
    """Raised when corrupted or contradictory cache data would affect a score."""


@dataclass(frozen=True)
class MissingnessPolicyResult:
    ranked: list[dict[str, Any]]
    audit_rows: list[dict[str, Any]]
    factor_rows: list[dict[str, Any]]
    amount_lineage_rows: list[dict[str, Any]]
    summary: dict[str, Any]


def _code(value: Any) -> str:
    return str(value or "").split(".", 1)[0].zfill(6)


def _q4(value: Any) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.0001")))


def _score(value: Any, minimum: float, maximum: float) -> float:
    return float(min_max_normalize(value, minimum, maximum))


def _number(value: Any, *, field: str, code: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DataPipelineError(f"DATA_PIPELINE_ERROR:{code}:{field}:NOT_NUMERIC") from exc
    if not math.isfinite(result):
        raise DataPipelineError(f"DATA_PIPELINE_ERROR:{code}:{field}:NON_FINITE")
    return result


def _current(record: Mapping[str, Any] | None, trade_key: str) -> bool:
    return bool(record) and str(record.get("trade_date") or "") == trade_key


def _classify_missing(
    code: str,
    daily: Mapping[str, Any] | None,
    basic: Mapping[str, Any] | None,
    history: Sequence[Mapping[str, Any]],
    pipeline_errors: Mapping[str, str],
    trade_key: str,
) -> tuple[str, str]:
    if code in pipeline_errors:
        return "DATA_PIPELINE_ERROR", pipeline_errors[code]
    if not _current(daily, trade_key):
        if len(history) < 20:
            return "DATA_INSUFFICIENT", f"HISTORY_BARS_{len(history)}_LT_20"
        return "NON_RANDOM_MISSING", "DAILY_MISSING_OR_TRADE_DATE_MISMATCH"
    amount = _number(daily.get("amount"), field="daily.amount", code=code)
    volume = _number(daily.get("vol"), field="daily.vol", code=code)
    if amount <= 0 or volume <= 0:
        return "NON_RANDOM_MISSING", "SUSPENDED_OR_NO_TRADE"
    if not _current(basic, trade_key):
        return "NON_RANDOM_MISSING", "DAILY_BASIC_MISSING_OR_TRADE_DATE_MISMATCH"
    return "NORMAL_SOURCE_MISSING", "MONEYFLOW_SOURCE_NOT_COVERED"


def _capital_inputs(
    code: str,
    daily: Mapping[str, Any],
    basic: Mapping[str, Any],
    flow: Mapping[str, Any] | None,
    *,
    trade_key: str,
) -> dict[str, dict[str, Any]]:
    raw_amount = _number(daily.get("amount"), field="daily.amount", code=code)
    amount_cny = raw_amount * 1000.0
    volume_ratio = _number(
        basic.get("volume_ratio"), field="daily_basic.volume_ratio", code=code
    )
    turnover = _number(
        basic.get("turnover_rate"), field="daily_basic.turnover_rate", code=code
    )
    result = {
        "amount_score": {
            "raw_value": raw_amount,
            "normalized_value": amount_cny,
            "score": _score(amount_cny, 50_000_000, 300_000_000),
        },
        "volume_ratio_score": {
            "raw_value": volume_ratio,
            "normalized_value": volume_ratio,
            "score": _score(volume_ratio, 0.8, 2.5),
        },
        "turnover_score": {
            "raw_value": turnover,
            "normalized_value": turnover,
            "score": _score(turnover, 0.5, 8),
        },
    }
    if flow is not None:
        if not _current(flow, trade_key):
            raise DataPipelineError(
                f"DATA_PIPELINE_ERROR:{code}:moneyflow.trade_date:MISMATCH"
            )
        raw_inflow = _number(
            flow.get("net_mf_amount"), field="moneyflow.net_mf_amount", code=code
        )
        inflow_cny = raw_inflow * 10000.0
        result["main_inflow_score"] = {
            "raw_value": raw_inflow,
            "normalized_value": inflow_cny,
            "score": _score(inflow_cny, -5_000_000, 20_000_000),
        }
    return result


def build_s2_1_missingness_policy(
    s2_rows: Sequence[Mapping[str, Any]],
    *,
    daily_by_code: Mapping[str, Mapping[str, Any]],
    basic_by_code: Mapping[str, Mapping[str, Any]],
    flow_by_code: Mapping[str, Mapping[str, Any]],
    histories: Mapping[str, Sequence[Mapping[str, Any]]],
    pipeline_errors: Mapping[str, str] | None = None,
    trade_key: str = TRADE_KEY,
    fail_on_pipeline_error: bool = True,
) -> MissingnessPolicyResult:
    pipeline_errors = pipeline_errors or {}
    scored: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    factors: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []

    for source in s2_rows:
        code = _code(source["stock_code"])
        daily = daily_by_code.get(code)
        basic = basic_by_code.get(code)
        flow = flow_by_code.get(code)
        history = histories.get(code) or ()
        old_capital = float(source["capital_score"])
        missing_class = "COMPLETE"
        missing_reason = None
        missing_subfactors: list[str] = []
        valid_subfactors: list[str] = []
        confidence = "COMPLETE"
        reweighted = False
        comparison_eligible = True
        score_status = "VALID"
        subfactor_values: dict[str, dict[str, Any]] = {}

        if flow is not None:
            if not daily or not basic:
                missing_class, missing_reason = (
                    "DATA_PIPELINE_ERROR",
                    "MONEYFLOW_PRESENT_WITHOUT_DAILY_OR_DAILY_BASIC",
                )
            else:
                try:
                    subfactor_values = _capital_inputs(
                        code, daily, basic, flow, trade_key=trade_key
                    )
                except DataPipelineError as exc:
                    missing_class, missing_reason = "DATA_PIPELINE_ERROR", str(exc)
                valid_subfactors = list(subfactor_values)
                new_capital = old_capital
        else:
            missing_class, missing_reason = _classify_missing(
                code, daily, basic, history, pipeline_errors, trade_key
            )
            if missing_class == "NORMAL_SOURCE_MISSING":
                try:
                    subfactor_values = _capital_inputs(
                        code,
                        daily or {},
                        basic or {},
                        None,
                        trade_key=trade_key,
                    )
                except DataPipelineError as exc:
                    missing_class, missing_reason = "DATA_PIPELINE_ERROR", str(exc)
                else:
                    valid_subfactors = list(subfactor_values)
                    missing_subfactors = ["main_inflow_score"]
                    new_capital = _q4(
                        sum(item["score"] for item in subfactor_values.values())
                        / len(subfactor_values)
                    )
                    confidence = "REWEIGHTED_NORMAL_MISSING"
                    reweighted = True
            if missing_class in {"NON_RANDOM_MISSING", "DATA_INSUFFICIENT"}:
                new_capital = old_capital
                missing_subfactors = [
                    "amount_score",
                    "volume_ratio_score",
                    "turnover_score",
                    "main_inflow_score",
                ]
                confidence = (
                    "NON_RANDOM_MISSING_BLOCKED"
                    if missing_class == "NON_RANDOM_MISSING"
                    else "DATA_INSUFFICIENT"
                )
                comparison_eligible = False
                score_status = "QUARANTINED_PRESERVE_S2"

        if missing_class == "DATA_PIPELINE_ERROR":
            if fail_on_pipeline_error:
                raise DataPipelineError(
                    missing_reason or f"DATA_PIPELINE_ERROR:{code}:UNKNOWN"
                )
            new_capital = old_capital
            missing_subfactors = [
                "amount_score",
                "volume_ratio_score",
                "turnover_score",
                "main_inflow_score",
            ]
            confidence = "INVALID"
            comparison_eligible = False
            score_status = "PIPELINE_ERROR_BLOCKED"

        coverage = len(valid_subfactors) / 4.0
        total_score = _q4(
            float(source["total_score"]) + (new_capital - old_capital) * 0.25
        )
        scored.append(
            {
                **dict(source),
                "stock_code": code,
                "capital_score": new_capital,
                "total_score": total_score,
                "version": VERSION_KEY,
                "factor_version": FACTOR_VERSION,
                "capital_data_coverage": coverage,
                "capital_confidence": confidence,
                "comparison_eligible": comparison_eligible,
            }
        )
        audits.append(
            {
                "stock_code": code,
                "missing_class": missing_class,
                "missing_reason": missing_reason,
                "missing_subfactors": missing_subfactors,
                "valid_subfactors": valid_subfactors,
                "capital_data_coverage": coverage,
                "capital_confidence": confidence,
                "reweighted": reweighted,
                "comparison_eligible": comparison_eligible,
                "score_status": score_status,
                "s2_capital_score": old_capital,
                "s2_1_capital_score": new_capital,
                "capital_score_delta": _q4(new_capital - old_capital),
                "s2_total_score": float(source["total_score"]),
                "s2_1_total_score": total_score,
                "quant_score_delta": _q4(total_score - float(source["total_score"])),
                "s2_rank": int(source["rank"]),
            }
        )

        effective_weight = 1.0 / len(valid_subfactors) if valid_subfactors else 0.0
        for name in (
            "amount_score",
            "volume_ratio_score",
            "turnover_score",
            "main_inflow_score",
        ):
            value = subfactor_values.get(name)
            missing = value is None
            weight = effective_weight if not missing else 0.0
            factors.append(
                {
                    "stock_code": code,
                    "factor_group": "capital",
                    "factor_name": name,
                    "raw_value": value["raw_value"] if value else None,
                    "normalized_value": value["normalized_value"] if value else None,
                    "score": value["score"] if value else 0.0,
                    "weight": weight,
                    "contribution": _q4((value["score"] if value else 0.0) * weight),
                    "missing_reason": missing_reason if missing else None,
                    "fallback_type": "EXCLUDED_REWEIGHTED"
                    if missing and reweighted
                    else "QUARANTINED_MISSING"
                    if missing
                    else None,
                    "score_origin": "REAL_CACHE"
                    if not missing
                    else "NO_NEUTRAL_SCORE_APPLIED",
                }
            )
        raw_amount = (
            float(daily.get("amount"))
            if daily and daily.get("amount") not in (None, "")
            else None
        )
        lineage.append(
            {
                "stock_code": code,
                "amount_raw": raw_amount,
                "amount_raw_unit": "THOUSAND_CNY",
                "amount_cny": raw_amount * 1000.0 if raw_amount is not None else None,
                "unit_conversion_version": AMOUNT_CONVERSION_VERSION,
            }
        )

    ranked = sorted(
        scored, key=lambda item: (-float(item["total_score"]), item["stock_code"])
    )
    rank_by_code: dict[str, int] = {}
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank
        rank_by_code[item["stock_code"]] = rank
    for row in audits:
        row["s2_1_rank"] = rank_by_code[row["stock_code"]]
        row["rank_delta"] = abs(row["s2_rank"] - row["s2_1_rank"])

    counts: dict[str, int] = {}
    confidence_counts: dict[str, int] = {}
    for row in audits:
        counts[row["missing_class"]] = counts.get(row["missing_class"], 0) + 1
        confidence_counts[row["capital_confidence"]] = (
            confidence_counts.get(row["capital_confidence"], 0) + 1
        )
    summary = {
        "stock_count": len(ranked),
        "moneyflow_missing_count": sum(row["missing_class"] != "COMPLETE" for row in audits),
        "normal_missing_count": counts.get("NORMAL_SOURCE_MISSING", 0),
        "non_random_missing_count": counts.get("NON_RANDOM_MISSING", 0)
        + counts.get("DATA_INSUFFICIENT", 0),
        "data_insufficient_count": counts.get("DATA_INSUFFICIENT", 0),
        "pipeline_error_count": counts.get("DATA_PIPELINE_ERROR", 0),
        "capital_reweighted_count": sum(bool(row["reweighted"]) for row in audits),
        "capital_confidence_distribution": confidence_counts,
        "data_coverage_distribution": {
            str(value): sum(
                math.isclose(float(row["capital_data_coverage"]), value)
                for row in audits
            )
            for value in sorted(
                {float(row["capital_data_coverage"]) for row in audits}
            )
        },
    }
    return MissingnessPolicyResult(ranked, audits, factors, lineage, summary)
