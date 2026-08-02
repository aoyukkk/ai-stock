from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import subprocess
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


ROOT = Path(__file__).resolve().parents[1]
START_DATE = "2026-07-17"
END_DATE = "2026-07-24"
EVALUATION_DATE = "2026-07-24"
BASELINE_VERSION = "TUSHARE_BASELINE_V1"
OUTPUT_DIR = ROOT / "outputs" / "weekly_review"
OUTPUT_PATH = OUTPUT_DIR / "首周推荐成功率_2026-07-17至2026-07-24.xlsx"
MIGRATION_PATH = (
    ROOT
    / "database"
    / "migrations"
    / "20260724_weekly_recommendation_review_v1.sql"
)
TRADE_DATES = (
    "2026-07-17",
    "2026-07-20",
    "2026-07-21",
    "2026-07-22",
    "2026-07-23",
    "2026-07-24",
)
OVERVIEW_RETURN_DATES = (
    "2026-07-20",
    "2026-07-21",
    "2026-07-22",
    "2026-07-23",
    "2026-07-24",
)
NEXT_TRADE_DATE = {
    "2026-07-17": "2026-07-20",
    "2026-07-20": "2026-07-21",
    "2026-07-21": "2026-07-22",
    "2026-07-22": "2026-07-23",
    "2026-07-23": "2026-07-24",
    "2026-07-24": "2026-07-27",
}
ELIGIBLE_CLASSES = {
    "STRONG_SUCCESS",
    "STABLE_SUCCESS",
    "OPPORTUNITY_HIT_GIVEBACK",
    "FAIL",
}
CAVEAT = (
    "本报告为算法运行首周的快速观察。最大涨幅仅表示潜在机会，不等于实际卖出收益；"
    "当前样本不足以证明模型长期有效。"
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _rows(connection: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(sql, tuple(params)).fetchall()]


def _load_cache(dataset: str, trade_date: str) -> list[dict[str, Any]]:
    key = trade_date.replace("-", "")
    path = ROOT / "data" / "cache" / "tushare" / "trade_date" / dataset / f"{key}.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else list(payload.get("records") or [])


def _by_code(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("ts_code") or row.get("stock_code") or "").upper(): row
        for row in rows
        if row.get("ts_code") or row.get("stock_code")
    }


def _normalize_ts_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        code, suffix = text.split(".", 1)
        return f"{code.zfill(6)}.{suffix}"
    code = text.zfill(6)
    if code.startswith(("4", "8", "920")):
        return f"{code}.BJ"
    if code.startswith("6"):
        return f"{code}.SH"
    return f"{code}.SZ"


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _latest_completed_pro_runs(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _rows(
        connection,
        """
        WITH ranked AS (
          SELECT p.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY base_trade_date
                   ORDER BY created_at DESC, id DESC
                 ) AS row_num
          FROM pro_resume_run p
          WHERE base_trade_date BETWEEN ? AND ?
            AND status = 'COMPLETED'
        )
        SELECT run_id,pipeline_run_id,quant_run_id,manifest_id,
               flash_validation_run_id,base_trade_date,target_trade_date,
               candidate_count,candidate_set_hash,top20_hash,manual_hash,
               pro_contract_version,prompt_version,portfolio_prompt_version,status
        FROM ranked
        WHERE row_num = 1
        ORDER BY base_trade_date
        """,
        (START_DATE, END_DATE),
    )
    actual = [str(row["base_trade_date"]) for row in rows]
    if actual != list(TRADE_DATES):
        raise RuntimeError(f"FORMAL_PRO_RUN_DATES_INCOMPLETE:{actual}")
    return rows


def _stock_metadata(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in _rows(connection, "SELECT code,name,industry FROM stock_master"):
        raw = str(row["code"]).upper()
        six = raw.split(".")[0]
        item = {
            "name": str(row.get("name") or ""),
            "industry": str(row.get("industry") or ""),
        }
        result[raw] = item
        result[six] = item
    return result


def _quant_rows(connection: sqlite3.Connection, quant_run_id: str) -> dict[str, dict[str, Any]]:
    return {
        _normalize_ts_code(row["stock_code"]): row
        for row in _rows(
            connection,
            """
            SELECT stock_code,rank,total_score,technical_score,capital_score,
                   emotion_score,momentum_score,risk_score
            FROM quant_rank_result
            WHERE quant_run_id = ?
            ORDER BY rank
            """,
            (quant_run_id,),
        )
    }


def _pro_rows(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    return _rows(
        connection,
        """
        SELECT stock_code,pro_score,pro_rank,priority,final_summary,
               review_status,ranking_version
        FROM pro_candidate_review
        WHERE pro_resume_run_id = ?
        ORDER BY COALESCE(pro_rank,999999),stock_code
        """,
        (run_id,),
    )


def _validation_plans(connection: sqlite3.Connection, validation_run_id: str) -> dict[str, dict[str, Any]]:
    return {
        _normalize_ts_code(row["stock_code"]): row
        for row in _rows(
            connection,
            """
            SELECT stock_code,status,actionable,recommended_price,max_acceptable_price,
                   stop_loss_price,take_profit_1_price,take_profit_2_price,
                   target_trade_date,cancel_conditions,warnings,temporal_status
            FROM model_validation_order_plan
            WHERE validation_run_id = ?
            """,
            (validation_run_id,),
        )
    }


def _actual_simulated_fills(
    connection: sqlite3.Connection, recommendation_date: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _rows(
        connection,
        """
        SELECT e.stock_code,e.evaluation_date,e.was_filled,e.simulated_fill_price,
               p.status,p.max_acceptable_price,p.stop_loss_price,p.plan_date
        FROM order_plan_evaluation e
        JOIN order_plan p ON p.id = e.order_plan_id
        WHERE p.plan_date = ? AND e.was_filled = 1
          AND e.simulated_fill_price IS NOT NULL
        ORDER BY e.created_at DESC,e.id DESC
        """,
        (recommendation_date,),
    ):
        result.setdefault(_normalize_ts_code(row["stock_code"]), row)
    return result


def _one_price_limit_up(
    daily_row: dict[str, Any], limit_row: dict[str, Any] | None
) -> bool:
    values = [_number(daily_row.get(key)) for key in ("open", "high", "low", "close")]
    if any(value is None for value in values):
        return False
    same_price = max(values) - min(values) <= 0.000001
    up_limit = _number((limit_row or {}).get("up_limit"))
    return bool(same_price and up_limit is not None and abs(values[0] - up_limit) <= 0.0001)


def _net_return(entry_price: float, exit_close: float, costs: dict[str, float]) -> float:
    notional = 100_000.0
    buy_commission = max(notional * costs["commission_rate"], costs["min_commission"])
    shares = (notional - buy_commission) / entry_price
    sell_gross = shares * exit_close * (1.0 - costs["slippage_rate"])
    sell_commission = max(
        sell_gross * costs["commission_rate"], costs["min_commission"]
    )
    stamp_tax = sell_gross * costs["stamp_tax_rate"]
    return (sell_gross - sell_commission - stamp_tax - notional) / notional


def _classify(mfe: float, current_net_return: float) -> str:
    if mfe >= 0.05 and current_net_return > 0:
        return "STRONG_SUCCESS"
    if mfe < 0.05 and current_net_return > 0:
        return "STABLE_SUCCESS"
    if mfe >= 0.05 and current_net_return <= 0:
        return "OPPORTUNITY_HIT_GIVEBACK"
    return "FAIL"


def _evaluate(
    *,
    recommendation_date: str,
    code: str,
    plan: dict[str, Any] | None,
    actual_fill: dict[str, Any] | None,
    daily_by_date: dict[str, dict[str, dict[str, Any]]],
    limit_by_date: dict[str, dict[str, dict[str, Any]]],
    costs: dict[str, float],
    pending: bool,
) -> dict[str, Any]:
    target = NEXT_TRADE_DATE[recommendation_date]
    planned_price = _number((plan or {}).get("recommended_price"))
    max_price = _number((plan or {}).get("max_acceptable_price"))
    stop_price = _number((plan or {}).get("stop_loss_price"))
    if pending:
        return {
            "target_trade_date": target,
            "entry_status": "PENDING",
            "entry_source": None,
            "entry_price": None,
            "planned_entry_price": planned_price,
            "max_acceptable_price": max_price,
            "stop_loss_price": stop_price,
            "current_net_return": None,
            "mfe": None,
            "mae": None,
            "giveback": None,
            "stop_hit": False,
            "risk_path_bad": False,
            "result_class": "PENDING",
            "eligible": False,
            "daily_returns": {},
            "path_note": "尚未完成T+1交易日",
        }

    target_row = daily_by_date.get(target, {}).get(code)
    if not target_row:
        return _not_tradable(target, max_price, stop_price, "T+1_DATA_MISSING")
    prices = {key: _number(target_row.get(key)) for key in ("open", "high", "low", "close")}
    volume = _number(target_row.get("vol"))
    if any(prices[key] is None or prices[key] <= 0 for key in prices) or not volume:
        return _not_tradable(target, max_price, stop_price, "SUSPENDED_OR_INVALID_BAR")
    if _one_price_limit_up(target_row, limit_by_date.get(target, {}).get(code)):
        return _not_tradable(target, max_price, stop_price, "ONE_PRICE_LIMIT_UP")

    blocked_statuses = {"CANCEL", "CANCELLED", "BLOCK", "BLOCKED", "REJECTED"}
    plan_status = str((plan or {}).get("status") or "").upper()
    if plan_status in blocked_statuses:
        return _not_tradable(target, max_price, stop_price, f"PLAN_{plan_status}")

    entry_price: float | None = None
    entry_source: str | None = None
    actual_price = _number((actual_fill or {}).get("simulated_fill_price"))
    if actual_price is not None and prices["low"] <= actual_price <= prices["high"]:
        entry_price = actual_price
        entry_source = "ACTUAL_SIMULATED_FILL"
    else:
        planned = planned_price
        if (
            planned is not None
            and prices["low"] <= planned <= prices["high"]
            and (max_price is None or planned <= max_price)
        ):
            entry_price = planned
            entry_source = "LEGAL_ORDER_PLAN_FILL"
        else:
            fallback = prices["open"] * (1.0 + costs["slippage_rate"])
            if max_price is not None and fallback > max_price:
                return _not_tradable(
                    target, max_price, stop_price, "T1_OPEN_ABOVE_MAX_ACCEPTABLE"
                )
            entry_price = fallback
            entry_source = "T1_OPEN_PLUS_BUY_SLIPPAGE"

    path_dates = [
        trade_date
        for trade_date in TRADE_DATES
        if target <= trade_date <= EVALUATION_DATE
    ]
    path_rows = [
        daily_by_date[trade_date][code]
        for trade_date in path_dates
        if code in daily_by_date.get(trade_date, {})
    ]
    if not path_rows or len(path_rows) != len(path_dates) or path_dates[-1] != EVALUATION_DATE:
        return _not_tradable(target, max_price, stop_price, "EVALUATION_PATH_INCOMPLETE")
    highs = [_number(row.get("high")) for row in path_rows]
    lows = [_number(row.get("low")) for row in path_rows]
    close = _number(daily_by_date[EVALUATION_DATE][code].get("close"))
    if any(value is None for value in highs + lows) or close is None:
        return _not_tradable(target, max_price, stop_price, "EVALUATION_PRICE_INVALID")

    mfe = max(highs) / entry_price - 1.0
    mae = min(lows) / entry_price - 1.0
    current_net_return = _net_return(entry_price, close, costs)
    daily_returns: dict[str, float] = {}
    previous_close: float | None = None
    for trade_date, row in zip(path_dates, path_rows):
        day_close = _number(row.get("close"))
        if day_close is None:
            return _not_tradable(target, max_price, stop_price, "EVALUATION_PRICE_INVALID")
        daily_returns[trade_date] = (
            day_close / entry_price - 1.0
            if previous_close is None
            else day_close / previous_close - 1.0
        )
        previous_close = day_close
    stop_hit = bool(stop_price is not None and min(lows) <= stop_price)
    ambiguous = False
    if stop_hit:
        for row in path_rows:
            day_low = _number(row.get("low"))
            day_high = _number(row.get("high"))
            if day_low <= stop_price and day_high / entry_price - 1.0 >= 0.05:
                ambiguous = True
                break
    result_class = (
        "PATH_AMBIGUOUS" if ambiguous else _classify(mfe, current_net_return)
    )
    return {
        "target_trade_date": target,
        "entry_status": "FILLED",
        "entry_source": entry_source,
        "entry_price": entry_price,
        "planned_entry_price": planned_price,
        "max_acceptable_price": max_price,
        "stop_loss_price": stop_price,
        "current_net_return": current_net_return,
        "mfe": mfe,
        "mae": mae,
        "giveback": mfe - current_net_return,
        "stop_hit": stop_hit,
        "risk_path_bad": stop_hit,
        "result_class": result_class,
        "eligible": result_class in ELIGIBLE_CLASSES,
        "daily_returns": daily_returns,
        "path_note": "同日触及止损与5%机会线，缺少分钟先后顺序" if ambiguous else "",
    }


def _not_tradable(
    target: str, max_price: float | None, stop_price: float | None, reason: str
) -> dict[str, Any]:
    return {
        "target_trade_date": target,
        "entry_status": "NOT_TRADABLE",
        "entry_source": None,
        "entry_price": None,
        "planned_entry_price": None,
        "max_acceptable_price": max_price,
        "stop_loss_price": stop_price,
        "current_net_return": None,
        "mfe": None,
        "mae": None,
        "giveback": None,
        "stop_hit": False,
        "risk_path_bad": False,
        "result_class": "NOT_TRADABLE",
        "eligible": False,
        "daily_returns": {},
        "path_note": reason,
    }


def _safe_mean(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.fmean(clean) if clean else None


def _safe_median(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.median(clean) if clean else None


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    classes = Counter(str(row["result_class"]) for row in rows)
    eligible = [row for row in rows if row["eligible"]]
    eligible_count = len(eligible)
    strong = classes["STRONG_SUCCESS"]
    stable = classes["STABLE_SUCCESS"]
    giveback = classes["OPPORTUNITY_HIT_GIVEBACK"]
    fail = classes["FAIL"]

    def rate(numerator: int) -> float | None:
        return numerator / eligible_count if eligible_count else None

    return {
        "recommendation_count": len(rows),
        "eligible_count": eligible_count,
        "pending": classes["PENDING"],
        "not_tradable": classes["NOT_TRADABLE"],
        "path_ambiguous": classes["PATH_AMBIGUOUS"],
        "strong_success": strong,
        "stable_success": stable,
        "opportunity_hit_giveback": giveback,
        "fail": fail,
        "broad_hit_rate": rate(strong + stable + giveback),
        "current_profit_rate": rate(strong + stable),
        "opportunity_capture_rate": rate(strong + giveback),
        "strong_success_rate": rate(strong),
        "failure_rate": rate(fail),
        "average_net_return": _safe_mean(row["current_net_return"] for row in eligible),
        "median_net_return": _safe_median(row["current_net_return"] for row in eligible),
        "average_mfe": _safe_mean(row["mfe"] for row in eligible),
        "average_mae": _safe_mean(row["mae"] for row in eligible),
        "average_giveback": _safe_mean(row["giveback"] for row in eligible),
        "maximum_gain": max(
            (row["current_net_return"] for row in eligible), default=None
        ),
        "maximum_loss": min(
            (row["current_net_return"] for row in eligible), default=None
        ),
        "stop_trigger_rate": (
            sum(bool(row["stop_hit"]) for row in eligible) / eligible_count
            if eligible_count
            else None
        ),
    }


def _group_metrics(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "UNKNOWN")].append(row)
    result = []
    for group, members in sorted(groups.items()):
        result.append({"group": group, **_aggregate(members)})
    return result


def _score_band(score: float | None) -> str:
    if score is None:
        return "NO_SCORE"
    if score >= 60:
        return ">=60"
    if score >= 55:
        return "55-60"
    if score >= 50:
        return "50-55"
    return "<50"


def _meets_recommendation_threshold(
    row: dict[str, Any], minimum_recommendation_score: float
) -> bool:
    pro_score = _number(row.get("pro_score"))
    return pro_score is not None and pro_score >= minimum_recommendation_score


def _deduplicate_overview(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["stock_code"])].append(row)

    overview: list[dict[str, Any]] = []
    for code, members in grouped.items():
        ordered = sorted(
            members,
            key=lambda item: (
                str(item["recommendation_date"]),
                int(item.get("pro_rank") or 999999),
            ),
        )
        primary = next(
            (item for item in ordered if item.get("entry_status") == "FILLED"),
            ordered[0],
        )
        recommendation_dates = sorted(
            {str(item["recommendation_date"]) for item in ordered}
        )
        overview.append(
            {
                **primary,
                "stock_code": code,
                "first_recommendation_date": recommendation_dates[0],
                "all_recommendation_dates": "、".join(recommendation_dates),
                "recommendation_count": len(ordered),
            }
        )
    return sorted(
        overview,
        key=lambda item: (
            str(item["first_recommendation_date"]),
            int(item.get("pro_rank") or 999999),
            str(item["stock_code"]),
        ),
    )


def _shadow_rows() -> list[dict[str, Any]]:
    report = (
        ROOT
        / "outputs"
        / "quant_bugfix_candidate_s2_1"
        / "2026-07-22"
        / "shadow-s2_1_missingness_policy-c3cfc487682ff3dd"
        / "quant_bugfix_candidate_report.json"
    )
    rows = [
        {
            "version": "S0_LEGACY",
            "sample_type": "RETROSPECTIVE_DIAGNOSTIC",
            "trade_date": "2026-07-22",
            "status": "FROZEN_FOR_FORWARD",
            "included_in_formal_rate": "否",
            "note": "正式历史基线；7/22仅回溯诊断。",
        },
        {
            "version": "S2_REAL_VOLUME_RATIO",
            "sample_type": "RETROSPECTIVE_DIAGNOSTIC",
            "trade_date": "2026-07-22",
            "status": "FROZEN_FOR_FORWARD",
            "included_in_formal_rate": "否",
            "note": "Bug修复候选；不自动晋级。",
        },
        {
            "version": "S2_1_MISSINGNESS_POLICY",
            "sample_type": "RETROSPECTIVE_DIAGNOSTIC",
            "trade_date": "2026-07-22",
            "status": "REPORT_PRESENT" if report.exists() else "REPORT_MISSING",
            "included_in_formal_rate": "否",
            "note": "缺失策略Shadow；不进入正式成功率。",
        },
        {
            "version": "S3_DIFFERENTIATED_EMOTION",
            "sample_type": "RETROSPECTIVE_DIAGNOSTIC",
            "trade_date": "2026-07-22",
            "status": "FROZEN_FOR_FORWARD",
            "included_in_formal_rate": "否",
            "note": "研究版本；不自动晋级。",
        },
        {
            "version": "S4_PERCENTILE_NORMALIZATION",
            "sample_type": "RETROSPECTIVE_DIAGNOSTIC",
            "trade_date": "2026-07-22",
            "status": "FROZEN_FOR_FORWARD",
            "included_in_formal_rate": "否",
            "note": "高漂移研究版本；不自动晋级。",
        },
        {
            "version": "Admission V2.2 / V3",
            "sample_type": "SHADOW_ONLY",
            "trade_date": "2026-07-24",
            "status": "COMPLETED_FORMAL_UNAFFECTED",
            "included_in_formal_rate": "否",
            "note": "只作门禁与解释对照。",
        },
        {
            "version": "Forward Shadow Backfill",
            "sample_type": "OUTCOME_BACKFILL",
            "trade_date": "2026-07-24",
            "status": "715_OUTCOMES_BACKFILLED",
            "included_in_formal_rate": "否",
            "note": "D1成熟713、D3成熟513、D5成熟213；公平A/B仍样本不足。",
        },
    ]
    return rows


def build_payload(connection: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    settings = yaml.safe_load((ROOT / "config" / "paper_trading.yaml").read_text(encoding="utf-8"))
    raw_costs = settings["paper_trading"]["cost"]
    costs = {
        "commission_rate": float(raw_costs["commission_rate"]),
        "min_commission": float(raw_costs["min_commission"]),
        "stamp_tax_rate": float(raw_costs["stamp_tax_rate"]),
        "slippage_rate": float(raw_costs["slippage_rate"]),
    }
    pro_runs = _latest_completed_pro_runs(connection)
    metadata = _stock_metadata(connection)
    daily_by_date = {
        trade_date: _by_code(_load_cache("daily", trade_date))
        for trade_date in TRADE_DATES
    }
    limit_by_date = {
        trade_date: _by_code(_load_cache("stk_limit", trade_date))
        for trade_date in TRADE_DATES
    }

    formal: list[dict[str, Any]] = []
    quant_top100: list[dict[str, Any]] = []
    source_audit: list[dict[str, Any]] = []
    for pro_run in pro_runs:
        recommendation_date = str(pro_run["base_trade_date"])
        quant = _quant_rows(connection, str(pro_run["quant_run_id"]))
        pro = _pro_rows(connection, str(pro_run["run_id"]))
        plans = _validation_plans(connection, str(pro_run["flash_validation_run_id"]))
        actual_fills = _actual_simulated_fills(connection, recommendation_date)
        if len(pro) != int(pro_run["candidate_count"]):
            raise RuntimeError(
                f"PRO_CANDIDATE_COUNT_MISMATCH:{recommendation_date}:{len(pro)}:"
                f"{pro_run['candidate_count']}"
            )
        source_audit.append(
            {
                **pro_run,
                "persisted_candidate_count": len(pro),
                "quant_detail_count": len(quant),
                "order_plan_count": len(plans),
                "actual_fill_count": len(actual_fills),
            }
        )
        pending = recommendation_date == EVALUATION_DATE
        for pro_row in pro:
            code = str(pro_row["stock_code"]).upper()
            quant_row = quant.get(code, {})
            info = metadata.get(code, metadata.get(code.split(".")[0], {}))
            evaluation = _evaluate(
                recommendation_date=recommendation_date,
                code=code,
                plan=plans.get(code),
                actual_fill=actual_fills.get(code),
                daily_by_date=daily_by_date,
                limit_by_date=limit_by_date,
                costs=costs,
                pending=pending,
            )
            formal.append(
                {
                    "source_type": "FORMAL_BASELINE",
                    "recommendation_date": recommendation_date,
                    "stock_code": code,
                    "stock_name": info.get("name", ""),
                    "industry": info.get("industry", ""),
                    "quant_run_id": pro_run["quant_run_id"],
                    "pro_run_id": pro_run["run_id"],
                    "quant_rank": quant_row.get("rank"),
                    "quant_score": _number(quant_row.get("total_score")),
                    "pro_rank": pro_row.get("pro_rank"),
                    "pro_score": _number(pro_row.get("pro_score")),
                    "recommendation_grade": pro_row.get("priority"),
                    "final_summary": pro_row.get("final_summary"),
                    **evaluation,
                }
            )

        for code, quant_row in sorted(quant.items(), key=lambda item: int(item[1]["rank"])):
            if int(quant_row["rank"]) > 100:
                continue
            info = metadata.get(code, metadata.get(code.split(".")[0], {}))
            evaluation = _evaluate(
                recommendation_date=recommendation_date,
                code=code,
                plan=None,
                actual_fill=None,
                daily_by_date=daily_by_date,
                limit_by_date=limit_by_date,
                costs=costs,
                pending=pending,
            )
            quant_top100.append(
                {
                    "source_type": "QUANT_TOP100",
                    "recommendation_date": recommendation_date,
                    "stock_code": code,
                    "stock_name": info.get("name", ""),
                    "industry": info.get("industry", ""),
                    "quant_run_id": pro_run["quant_run_id"],
                    "pro_run_id": None,
                    "quant_rank": quant_row.get("rank"),
                    "quant_score": _number(quant_row.get("total_score")),
                    "pro_rank": None,
                    "pro_score": None,
                    "recommendation_grade": None,
                    "final_summary": "",
                    **evaluation,
                }
            )

    for row in formal:
        row["quant_score_band"] = _score_band(row["quant_score"])
    summary = _aggregate(formal)
    summary["sample_status"] = (
        "INSUFFICIENT_SAMPLE" if summary["eligible_count"] < 50 else "SUFFICIENT_SAMPLE"
    )
    summary["conclusion"] = (
        "首周快速观察，不构成长期有效性证明。"
        if summary["sample_status"] == "INSUFFICIENT_SAMPLE"
        else "首周样本已达到50条，但仍不构成长期有效性证明。"
    )
    quant_summary = _aggregate(quant_top100)
    today = [row for row in formal if row["recommendation_date"] == EVALUATION_DATE]
    payload = {
        "phase": "2026-07-24 Friday Post-Close Run + Weekly Recommendation Review",
        "review_start_date": START_DATE,
        "review_end_date": END_DATE,
        "evaluation_date": EVALUATION_DATE,
        "baseline_version": BASELINE_VERSION,
        "caveat": CAVEAT,
        "summary": summary,
        "quant_top100_summary": quant_summary,
        "formal_rows": formal,
        "today_rows": today,
        "mfe_rows": [row for row in formal if row["eligible"]],
        "giveback_rows": [
            row
            for row in formal
            if row["result_class"] == "OPPORTUNITY_HIT_GIVEBACK"
        ],
        "fail_rows": [row for row in formal if row["result_class"] == "FAIL"],
        "excluded_rows": [
            row
            for row in formal
            if row["result_class"] in {"PENDING", "NOT_TRADABLE", "PATH_AMBIGUOUS"}
        ],
        "by_date": _group_metrics(formal, "recommendation_date"),
        "by_grade": _group_metrics(formal, "recommendation_grade"),
        "by_score_band": _group_metrics(formal, "quant_score_band"),
        "comparison_rows": [
            {"selection": "正式候选", **summary},
            {"selection": "Quant Top100", **quant_summary},
        ],
        "shadow_rows": _shadow_rows(),
        "calculation_rules": [
            ["统计范围", f"{START_DATE} 至 {END_DATE}；仅正式Baseline推荐进入正式统计。"],
            ["EntryPrice优先级", "合法实际模拟成交价 > 合法订单计划成交价 > T+1开盘价×(1+买入滑点)。"],
            ["不可交易", "停牌、一字涨停、超过最高接受价、盘前CANCEL/BLOCK、数据不足均记NOT_TRADABLE。"],
            ["成熟样本", "至少完成一个完整T+1交易日；今日推荐为PENDING且不进入分母。"],
            ["CurrentNetReturn", "按10万元统一评价名义本金，扣买卖滑点、双边佣金和卖出印花税。"],
            ["MFE", "合法买入日起至2026-07-24期间最高价/EntryPrice-1。"],
            ["MAE", "合法买入日起至2026-07-24期间最低价/EntryPrice-1。"],
            ["Giveback", "MFE-CurrentNetReturn；最大涨幅不是实际卖出收益。"],
            ["PATH_AMBIGUOUS", "同日触及止损且达到5%机会线，但无分钟数据判定先后，排除分母。"],
            ["EligibleCount", "STRONG_SUCCESS+STABLE_SUCCESS+OPPORTUNITY_HIT_GIVEBACK+FAIL。"],
            ["首周宽口径命中率", "(STRONG_SUCCESS+STABLE_SUCCESS+OPPORTUNITY_HIT_GIVEBACK)/EligibleCount。"],
            ["当前盈利率", "(STRONG_SUCCESS+STABLE_SUCCESS)/EligibleCount。"],
            ["5%机会捕获率", "(STRONG_SUCCESS+OPPORTUNITY_HIT_GIVEBACK)/EligibleCount。"],
            ["强成功率", "STRONG_SUCCESS/EligibleCount。"],
            ["失败率", "FAIL/EligibleCount。"],
            ["费用参数", json.dumps(costs, ensure_ascii=False, sort_keys=True)],
        ],
        "source_audit": source_audit,
        "daily_cache_audit": [
            {
                "trade_date": trade_date,
                "daily_count": len(daily_by_date[trade_date]),
                "stk_limit_count": len(limit_by_date[trade_date]),
                "daily_hash": _sha256_file(
                    ROOT
                    / "data"
                    / "cache"
                    / "tushare"
                    / "trade_date"
                    / "daily"
                    / f"{trade_date.replace('-', '')}.json"
                ),
            }
            for trade_date in TRADE_DATES
        ],
        "audit": {
            "formal_recommendation_count": len(formal),
            "formal_db_detail_expected": len(formal),
            "quant_top100_count": len(quant_top100),
            "db_detail_expected": len(formal) + len(quant_top100),
            "today_pending_count": len(today),
            "sheet_count_expected": 14,
            "formula_error_expected": 0,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
            "llm_calls_for_weekly_report": 0,
            "external_api_calls_for_weekly_report": 0,
            "formal_quant_hash_changed": False,
            "flash_prompt_hash_changed": False,
            "pro_prompt_hash_changed": False,
            "database_excel_reconciliation": "ENFORCED_BEFORE_FINALIZE",
        },
    }
    payload["input_hash"] = _canonical_hash(
        {
            "pro_runs": source_audit,
            "formal_rows": formal,
            "quant_top100_rows": quant_top100,
            "daily_cache_audit": payload["daily_cache_audit"],
            "costs": costs,
        }
    )
    payload["run_id"] = f"weekly-review-{payload['input_hash'][:16]}"
    payload["content_hash"] = _canonical_hash(
        {
            "summary": summary,
            "formal_rows": formal,
            "quant_top100_summary": quant_summary,
        }
    )
    return payload, formal + quant_top100


def build_simple_payload(
    connection: sqlite3.Connection,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build the compact user-facing review without changing formal source data."""
    settings = yaml.safe_load(
        (ROOT / "config" / "paper_trading.yaml").read_text(encoding="utf-8")
    )
    raw_costs = settings["paper_trading"]["cost"]
    costs = {
        "commission_rate": float(raw_costs["commission_rate"]),
        "min_commission": float(raw_costs["min_commission"]),
        "stamp_tax_rate": float(raw_costs["stamp_tax_rate"]),
        "slippage_rate": float(raw_costs["slippage_rate"]),
    }
    review_settings = yaml.safe_load(
        (ROOT / "config" / "review.yaml").read_text(encoding="utf-8")
    )
    minimum_recommendation_score = float(
        review_settings.get("selection_performance", {}).get(
            "minimum_recommendation_score", 60
        )
    )
    pro_runs = _latest_completed_pro_runs(connection)
    metadata = _stock_metadata(connection)
    daily_by_date = {
        trade_date: _by_code(_load_cache("daily", trade_date))
        for trade_date in TRADE_DATES
    }
    limit_by_date = {
        trade_date: _by_code(_load_cache("stk_limit", trade_date))
        for trade_date in TRADE_DATES
    }

    formal: list[dict[str, Any]] = []
    source_audit: list[dict[str, Any]] = []
    for pro_run in pro_runs:
        recommendation_date = str(pro_run["base_trade_date"])
        quant = _quant_rows(connection, str(pro_run["quant_run_id"]))
        pro = _pro_rows(connection, str(pro_run["run_id"]))
        plans = _validation_plans(connection, str(pro_run["flash_validation_run_id"]))
        actual_fills = _actual_simulated_fills(connection, recommendation_date)
        if len(pro) != int(pro_run["candidate_count"]):
            raise RuntimeError(
                f"PRO_CANDIDATE_COUNT_MISMATCH:{recommendation_date}:{len(pro)}:"
                f"{pro_run['candidate_count']}"
            )
        source_audit.append(
            {
                **pro_run,
                "persisted_candidate_count": len(pro),
                "quant_detail_count": len(quant),
                "order_plan_count": len(plans),
                "actual_fill_count": len(actual_fills),
            }
        )
        pending = recommendation_date == EVALUATION_DATE
        for pro_row in pro:
            code = _normalize_ts_code(pro_row["stock_code"])
            quant_row = quant.get(code, {})
            info = metadata.get(code, metadata.get(code.split(".")[0], {}))
            evaluation = _evaluate(
                recommendation_date=recommendation_date,
                code=code,
                plan=plans.get(code),
                actual_fill=actual_fills.get(code),
                daily_by_date=daily_by_date,
                limit_by_date=limit_by_date,
                costs=costs,
                pending=pending,
            )
            formal.append(
                {
                    "source_type": "FORMAL_BASELINE",
                    "recommendation_date": recommendation_date,
                    "stock_code": code,
                    "stock_name": info.get("name", ""),
                    "industry": info.get("industry", ""),
                    "quant_run_id": pro_run["quant_run_id"],
                    "pro_run_id": pro_run["run_id"],
                    "quant_rank": quant_row.get("rank"),
                    "quant_score": _number(quant_row.get("total_score")),
                    "pro_rank": pro_row.get("pro_rank"),
                    "pro_score": _number(pro_row.get("pro_score")),
                    "recommendation_grade": pro_row.get("priority"),
                    "final_summary": pro_row.get("final_summary"),
                    **evaluation,
                }
            )

    formal = [
        row
        for row in formal
        if _meets_recommendation_threshold(row, minimum_recommendation_score)
    ]
    for row in formal:
        row["quant_score_band"] = _score_band(row["quant_score"])
    overview_rows = _deduplicate_overview(formal)
    summary = _aggregate(overview_rows)
    summary["sample_status"] = (
        "INSUFFICIENT_SAMPLE"
        if summary["eligible_count"] < 50
        else "SUFFICIENT_SAMPLE"
    )
    summary["conclusion"] = (
        "首周去重快速观察，不构成长期有效性证明。"
    )
    today = [row for row in formal if row["recommendation_date"] == EVALUATION_DATE]
    date_sheets = [
        {
            "trade_date": trade_date,
            "rows": [
                row for row in formal if row["recommendation_date"] == trade_date
            ],
        }
        for trade_date in TRADE_DATES
    ]
    four_classes = [
        {"key": "STRONG_SUCCESS", "label": "强成功"},
        {"key": "STABLE_SUCCESS", "label": "稳定成功"},
        {"key": "OPPORTUNITY_HIT_GIVEBACK", "label": "机会命中后回吐"},
        {"key": "FAIL", "label": "失败"},
    ]
    payload = {
        "phase": "2026-07-24 Compact Weekly Recommendation Review",
        "review_start_date": START_DATE,
        "review_end_date": END_DATE,
        "evaluation_date": EVALUATION_DATE,
        "baseline_version": BASELINE_VERSION,
        "minimum_recommendation_score": minimum_recommendation_score,
        "recommendation_score_field": "pro_score",
        "caveat": CAVEAT,
        "summary": summary,
        "overview_rows": overview_rows,
        "overview_return_dates": list(OVERVIEW_RETURN_DATES),
        "date_sheets": date_sheets,
        "four_classes": four_classes,
        "formal_rows": formal,
        "today_rows": today,
        "source_audit": source_audit,
        "daily_cache_audit": [
            {
                "trade_date": trade_date,
                "daily_count": len(daily_by_date[trade_date]),
                "stk_limit_count": len(limit_by_date[trade_date]),
                "daily_hash": _sha256_file(
                    ROOT
                    / "data"
                    / "cache"
                    / "tushare"
                    / "trade_date"
                    / "daily"
                    / f"{trade_date.replace('-', '')}.json"
                ),
            }
            for trade_date in TRADE_DATES
        ],
        "audit": {
            "formal_recommendation_count": len(formal),
            "unique_stock_count": len(overview_rows),
            "formal_db_detail_expected": len(formal),
            "db_detail_expected": len(formal),
            "today_pending_count": len(today),
            "sheet_count_expected": 2 + len(date_sheets),
            "chart_count_expected": 1,
            "formula_error_expected": 0,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
            "llm_calls_for_weekly_report": 0,
            "external_api_calls_for_weekly_report": 0,
            "formal_quant_hash_changed": False,
            "flash_prompt_hash_changed": False,
            "pro_prompt_hash_changed": False,
            "database_excel_reconciliation": "ENFORCED_BEFORE_FINALIZE",
        },
    }
    payload["input_hash"] = _canonical_hash(
        {
            "layout_version": "COMPACT_WEEKLY_REVIEW_V4_PRO_SCORE_GE_60",
            "minimum_recommendation_score": minimum_recommendation_score,
            "pro_runs": source_audit,
            "formal_rows": formal,
            "daily_cache_audit": payload["daily_cache_audit"],
            "costs": costs,
        }
    )
    payload["run_id"] = f"weekly-review-{payload['input_hash'][:16]}"
    payload["content_hash"] = _canonical_hash(
        {
            "summary": summary,
            "overview_rows": overview_rows,
            "formal_rows": formal,
        }
    )
    return payload, formal


def _build_workbook(payload: dict[str, Any], output_path: Path, preview_dir: Path) -> dict[str, Any]:
    build_dir = output_path.parent / f".weekly-build-{uuid.uuid4().hex}"
    build_dir.mkdir(parents=True, exist_ok=False)
    link = build_dir / "node_modules"
    try:
        builder = build_dir / "build_weekly_recommendation_review.mjs"
        shutil.copy2(ROOT / "scripts" / "build_weekly_recommendation_review.mjs", builder)
        payload_path = build_dir / "payload.json"
        qa_path = build_dir / "qa.json"
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
        )
        dependency_root = (
            Path.home()
            / ".cache"
            / "codex-runtimes"
            / "codex-primary-runtime"
            / "dependencies"
            / "node"
            / "node_modules"
        )
        if not (dependency_root / "@oai" / "artifact-tool").exists():
            raise RuntimeError("ARTIFACT_TOOL_DEPENDENCY_NOT_FOUND")
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(dependency_root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if junction.returncode != 0:
            raise RuntimeError(f"ARTIFACT_TOOL_JUNCTION_FAILED:{junction.stderr}")
        completed = subprocess.run(
            [
                "node",
                str(builder),
                str(payload_path),
                str(output_path),
                str(preview_dir),
                str(qa_path),
            ],
            cwd=build_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if not output_path.is_file():
            raise RuntimeError(
                f"WEEKLY_WORKBOOK_EXPORT_FAILED:{completed.returncode}:"
                f"{completed.stderr[-1500:]}"
            )
        if not qa_path.is_file():
            raise RuntimeError("WEEKLY_WORKBOOK_QA_MISSING")
        return json.loads(qa_path.read_text(encoding="utf-8"))
    finally:
        if link.exists():
            os.rmdir(link)
        shutil.rmtree(build_dir, ignore_errors=True)


def _persist(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    details: list[dict[str, Any]],
    workbook_hash: str,
    workbook_path: Path = OUTPUT_PATH,
) -> dict[str, Any]:
    connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
    existing = connection.execute(
        """
        SELECT run_id,input_hash,workbook_hash
        FROM weekly_recommendation_review_run
        WHERE run_id = ?
        """,
        (payload["run_id"],),
    ).fetchone()
    if existing:
        if existing[1] != payload["input_hash"] or existing[2] != workbook_hash:
            raise RuntimeError("IMMUTABLE_WEEKLY_REVIEW_CONFLICT")
        run_count = connection.execute(
            "SELECT COUNT(1) FROM weekly_recommendation_review_run WHERE run_id = ?",
            (payload["run_id"],),
        ).fetchone()[0]
        detail_count = connection.execute(
            "SELECT COUNT(1) FROM weekly_recommendation_review_detail WHERE run_id = ?",
            (payload["run_id"],),
        ).fetchone()[0]
        return {"reused": True, "run_count": run_count, "detail_count": detail_count}

    now = datetime.now(timezone.utc).isoformat()
    summary = payload["summary"]
    connection.execute(
        """
        INSERT INTO weekly_recommendation_review_run (
          run_id,review_start_date,review_end_date,evaluation_date,baseline_version,
          status,sample_status,input_hash,content_hash,workbook_path,workbook_hash,
          summary,audit,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload["run_id"],
            START_DATE,
            END_DATE,
            EVALUATION_DATE,
            BASELINE_VERSION,
            "COMPLETED",
            summary["sample_status"],
            payload["input_hash"],
            payload["content_hash"],
            str(workbook_path.resolve()),
            workbook_hash,
            json.dumps(summary, ensure_ascii=False, default=str),
            json.dumps(payload["audit"], ensure_ascii=False, default=str),
            now,
            now,
        ),
    )
    sql = """
        INSERT INTO weekly_recommendation_review_detail (
          run_id,source_type,recommendation_date,target_trade_date,stock_code,
          stock_name,industry,quant_run_id,pro_run_id,quant_rank,quant_score,
          pro_rank,pro_score,recommendation_grade,entry_status,entry_source,
          entry_price,max_acceptable_price,stop_loss_price,current_net_return,
          mfe,mae,giveback,stop_hit,risk_path_bad,result_class,eligible,
          metadata_json,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    for row in details:
        metadata = {
            "path_note": row.get("path_note"),
            "quant_score_band": row.get("quant_score_band"),
            "final_summary": row.get("final_summary"),
        }
        connection.execute(
            sql,
            (
                payload["run_id"],
                row["source_type"],
                row["recommendation_date"],
                row.get("target_trade_date"),
                row["stock_code"],
                row.get("stock_name"),
                row.get("industry"),
                row.get("quant_run_id"),
                row.get("pro_run_id"),
                row.get("quant_rank"),
                row.get("quant_score"),
                row.get("pro_rank"),
                row.get("pro_score"),
                row.get("recommendation_grade"),
                row["entry_status"],
                row.get("entry_source"),
                row.get("entry_price"),
                row.get("max_acceptable_price"),
                row.get("stop_loss_price"),
                row.get("current_net_return"),
                row.get("mfe"),
                row.get("mae"),
                row.get("giveback"),
                int(bool(row.get("stop_hit"))),
                int(bool(row.get("risk_path_bad"))),
                row["result_class"],
                int(bool(row.get("eligible"))),
                json.dumps(metadata, ensure_ascii=False, default=str),
                now,
                now,
            ),
        )
    connection.commit()
    invalid_runs = {
        "weekly-review-5791ac0ce7271307": "QUANT_TOP100_STOCK_CODE_NOT_NORMALIZED",
        "weekly-review-3ce8fb1dcb039eee": "ORDER_PLAN_STOCK_CODE_NOT_NORMALIZED",
        "weekly-review-a91cecf9d05efeee": "MONDAY_PLANNED_ENTRY_PRICE_NOT_DISPLAYED",
        "weekly-review-bdb9551dd6d8a84b": "USER_REQUESTED_SIMPLIFIED_WEEKLY_LAYOUT",
        "weekly-review-74d290fee654a221": "A_SHARE_RETURN_COLOR_CONVENTION_RED_UP_GREEN_DOWN",
        "weekly-review-5a026f8c4665348a": "RECOMMENDATION_SCOPE_MUST_BE_PRO_SCORE_GE_60",
    }
    for invalid_run_id, reason in invalid_runs.items():
        invalid_exists = connection.execute(
            "SELECT COUNT(1) FROM weekly_recommendation_review_run WHERE run_id = ?",
            (invalid_run_id,),
        ).fetchone()[0]
        if not invalid_exists or invalid_run_id == payload["run_id"]:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO weekly_recommendation_review_supersession (
              superseded_run_id,replacement_run_id,reason,created_at
            ) VALUES (?,?,?,?)
            """,
            (invalid_run_id, payload["run_id"], reason, now),
        )
        connection.commit()
    run_count = connection.execute(
        "SELECT COUNT(1) FROM weekly_recommendation_review_run WHERE run_id = ?",
        (payload["run_id"],),
    ).fetchone()[0]
    detail_count = connection.execute(
        "SELECT COUNT(1) FROM weekly_recommendation_review_detail WHERE run_id = ?",
        (payload["run_id"],),
    ).fetchone()[0]
    if run_count != 1 or detail_count != len(details):
        raise RuntimeError(
            f"DATABASE_EXCEL_RECONCILIATION_FAILED:{run_count}:{detail_count}:{len(details)}"
        )
    return {"reused": False, "run_count": run_count, "detail_count": detail_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成首周正式推荐复盘工作簿和独立数据库记录")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "ai_trader_dev.db")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    database = args.database.resolve()
    output_path = args.output.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"DATABASE_NOT_FOUND:{database}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    preview_dir = OUTPUT_DIR / "preview_2026-07-17_2026-07-24"
    candidate = OUTPUT_DIR / f".weekly-review-{uuid.uuid4().hex[:10]}.xlsx"
    connection = sqlite3.connect(database)
    try:
        payload, details = build_simple_payload(connection)
        payload_path = OUTPUT_DIR / f"{payload['run_id']}_payload.json"
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        qa = _build_workbook(payload, candidate, preview_dir)
        if (
            qa.get("sheet_count") != payload["audit"]["sheet_count_expected"]
            or qa.get("chart_count") != payload["audit"]["chart_count_expected"]
            or qa.get("formula_error_count") != 0
        ):
            raise RuntimeError(f"WEEKLY_WORKBOOK_QA_FAILED:{qa}")
        candidate_hash = _sha256_file(candidate)
        if output_path.exists():
            if _sha256_file(output_path) != candidate_hash:
                raise RuntimeError("IMMUTABLE_WORKBOOK_PATH_CONFLICT")
            candidate.unlink(missing_ok=True)
            workbook_hash = _sha256_file(output_path)
            workbook_reused = True
        else:
            candidate.replace(output_path)
            workbook_hash = candidate_hash
            workbook_reused = False
        database_result = _persist(
            connection, payload, details, workbook_hash, output_path
        )
        report = {
            "phase": payload["phase"],
            "run_id": payload["run_id"],
            "status": "COMPLETED",
            "sample_status": payload["summary"]["sample_status"],
            "summary": payload["summary"],
            "workbook": str(output_path),
            "workbook_hash": workbook_hash,
            "workbook_reused": workbook_reused,
            "workbook_qa": qa,
            "database": str(database),
            "database_result": database_result,
            "input_hash": payload["input_hash"],
            "content_hash": payload["content_hash"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        report_path = OUTPUT_DIR / f"{payload['run_id']}_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        connection.close()
        candidate.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
