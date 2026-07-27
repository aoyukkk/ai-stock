from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import yaml
from scipy.stats import kendalltau, spearmanr

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.normalizer import average_score, min_max_normalize, percentile_score
from scripts.audit_quant_factor_scores import (
    WEIGHTS,
    canonical_hash,
    distribution,
    file_sha256,
    normalize_code,
)


BASE_RUN_ID = "quant-dd980474bdd269a46a47ddf4"
TRADE_DATE = "2026-07-22"
TRADE_KEY = "20260722"
BASE_INPUT_HASH = (
    "dd980474bdd269a46a47ddf441f1ce52dae355c0335caf14e4838ea0bb069ff2"
)
BASE_FACTOR_VERSION = "v0.3-phase4"
BASE_REPORT = (
    ROOT / "data" / "reports" / "quant_20260722_desktop-271aff624e614b1e94aa.json"
)
BASE_DATABASE = ROOT / "data" / "ai_trader_dev.db"
BASE_WORKBOOK = (
    ROOT / "outputs" / "2026-07-22" / "正式日线" / "智能交易助手_2026-07-22.xlsx"
)
BASE_AUDIT = (
    ROOT
    / "outputs"
    / "quant_factor_audit"
    / BASE_RUN_ID
    / "quant_factor_audit.json"
)
SHADOW_CONFIG = ROOT / "config" / "shadow_quant_factor_v1.yaml"
SHADOW_MIGRATION = (
    ROOT
    / "database"
    / "migrations"
    / "20260724_quant_shadow_counterfactual_v1.sql"
)
DEFAULT_OUTPUT = (
    ROOT / "outputs" / "quant_factor_shadow_research" / TRADE_DATE
)
DEFAULT_RESEARCH_DB = ROOT / "data" / "quant_shadow_research.db"
STOCK_BASIC_CACHE = (
    ROOT
    / "data"
    / "cache"
    / "tushare"
    / "stock_basic_9ca2d6c8198c35324085347fb0446f49f64e9ebd.json"
)

VERSIONS = (
    "S0_LEGACY",
    "S1_AMOUNT_UNIT_FIX",
    "S2_REAL_VOLUME_RATIO",
    "S3_GLOBAL_EMOTION_REAL",
    "S3_DIFFERENTIATED_EMOTION",
    "S4_PERCENTILE_NORMALIZATION",
)


def q4(value: Any) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.0001")))


def amount_to_cny(value: Any, unit: str = "THOUSAND_CNY") -> tuple[float, str]:
    numeric = float(value or 0)
    normalized = str(unit).upper()
    if normalized in {"CNY", "YUAN", "RMB"}:
        return numeric, "ALREADY_CNY"
    if normalized == "THOUSAND_CNY":
        return numeric * 1000.0, "MULTIPLIED_BY_1000"
    if normalized == "TEN_THOUSAND_CNY":
        return numeric * 10000.0, "MULTIPLIED_BY_10000"
    raise ValueError(f"UNKNOWN_AMOUNT_UNIT:{unit}")


def score_fixed(
    value: Any, minimum: Any, maximum: Any, higher_is_better: bool = True
) -> float:
    return float(
        min_max_normalize(
            value, minimum, maximum, higher_is_better=higher_is_better
        )
    )


def score_average(values: Iterable[Any]) -> float:
    return float(average_score(values))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    if not headers:
        headers = ["status"]
        rows = [{"status": "NO_DATA"}]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def source_hashes() -> dict[str, str]:
    quant_files = (
        ROOT / "quant" / "config.py",
        ROOT / "quant" / "factors.py",
        ROOT / "quant" / "normalizer.py",
        ROOT / "quant" / "ranking.py",
        ROOT / "config" / "quant_factor.yaml",
    )
    return {
        "quant": canonical_hash(
            {
                str(path.relative_to(ROOT)): file_sha256(path)
                for path in quant_files
            }
        ),
        "shadow_script": file_sha256(Path(__file__)),
        "shadow_config": file_sha256(SHADOW_CONFIG),
    }


def open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def rows(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def cache_rows(dataset: str, trade_key: str = TRADE_KEY) -> list[dict[str, Any]]:
    path = (
        ROOT
        / "data"
        / "cache"
        / "tushare"
        / "trade_date"
        / dataset
        / f"{trade_key}.json"
    )
    return read_json(path) if path.exists() else []


def by_code(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in records:
        code = normalize_code(row.get("ts_code") or row.get("stock_code"))
        if not code:
            continue
        current = result.get(code)
        if current is None or str(row.get("trade_date") or "") >= str(
            current.get("trade_date") or ""
        ):
            result[code] = row
    return result


def daily_history(start_key: str = "20260607") -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    root = ROOT / "data" / "cache" / "tushare" / "trade_date" / "daily"
    for path in sorted(root.glob("*.json")):
        if not path.stem.isdigit() or path.stem < start_key or path.stem > TRADE_KEY:
            continue
        for row in read_json(path):
            code = normalize_code(row.get("ts_code"))
            if code:
                result[code].append(row)
    for values in result.values():
        values.sort(key=lambda item: str(item.get("trade_date") or ""))
    return result


def rank_version(version: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        (dict(item) for item in items),
        key=lambda item: (-float(item["total_score"]), normalize_code(item["stock_code"])),
    )
    for index, item in enumerate(ranked, 1):
        item["rank"] = index
        item["version"] = version
    return ranked


def weighted_total(item: dict[str, Any]) -> float:
    return q4(
        sum(float(item[f"{factor}_score"]) * float(weight) for factor, weight in WEIGHTS.items())
    )


def percentile_map(values: dict[str, float]) -> dict[str, float]:
    universe = list(values.values())
    return {
        code: float(percentile_score(value, universe, higher_is_better=True))
        for code, value in values.items()
    }


def build_universe_audit(
    stock_master: list[dict[str, Any]],
    histories: dict[str, list[dict[str, Any]]],
    base_codes: set[str],
    min_bars: int,
    amount_threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    audit: list[dict[str, Any]] = []
    stages = Counter()
    for stock in stock_master:
        code = normalize_code(stock.get("ts_code") or stock.get("symbol"))
        name = str(stock.get("name") or code)
        status = str(stock.get("list_status") or "").upper()
        history = histories.get(code, [])
        latest = history[-1] if history else {}
        raw_amount = float(latest.get("amount") or 0)
        normalized_amount, _ = amount_to_cny(raw_amount)
        if code in base_codes:
            stage = "SELECTED"
            reason = (
                "formal_effective_universe"
                if len(history) >= min_bars
                else "formal_effective_universe_with_historical_backup"
            )
        elif not code or len(code) != 6:
            stage, reason = "INVALID_CODE", "exclude_empty_or_invalid_code"
        elif "ST" in name.upper():
            stage, reason = "ST", "exclude_st"
        elif status not in {"L", "LISTED", "NORMAL", "1"}:
            stage, reason = "STATUS", "exclude_non_normal_status"
        elif len(history) < min_bars:
            stage, reason = "HISTORY", f"require_min_kline_bars:{len(history)}<{min_bars}"
        else:
            stage, reason = "OTHER", "formal_run_skipped_or_failed_after_filter"
        stages[stage] += 1
        legacy_gate_pass = raw_amount >= amount_threshold
        corrected_gate_pass = normalized_amount >= amount_threshold
        audit.append(
            {
                "stock_code": code,
                "stock_name": name,
                "exclusion_stage": stage,
                "exclusion_reason": reason,
                "history_bar_count": len(history),
                "raw_amount": raw_amount,
                "raw_unit": "THOUSAND_CNY",
                "normalized_amount": normalized_amount,
                "normalized_unit": "CNY",
                "threshold": amount_threshold,
                "production_amount_gate_wired": False,
                "legacy_configured_gate_pass": legacy_gate_pass,
                "corrected_configured_gate_pass": corrected_gate_pass,
                "unit_fix_changes_result": legacy_gate_pass != corrected_gate_pass,
                "final_status": "INCLUDED" if code in base_codes else "EXCLUDED",
            }
        )
    configured_base = [row for row in audit if row["final_status"] == "INCLUDED"]
    legacy_pass = {
        row["stock_code"] for row in configured_base if row["legacy_configured_gate_pass"]
    }
    corrected_pass = {
        row["stock_code"]
        for row in configured_base
        if row["corrected_configured_gate_pass"]
    }
    summary = {
        "raw_stock_master_count": len(stock_master),
        "formal_effective_universe_count": len(base_codes),
        "stage_counts": dict(stages),
        "natural_universe_legacy": len(base_codes),
        "natural_universe_corrected": len(base_codes),
        "formal_universe_flip_count_after_amount_fix": 0,
        "formal_reason": "production min_daily_amount gate is configured but not called",
        "configured_gate_legacy_unit_pass_count": len(legacy_pass),
        "configured_gate_corrected_unit_pass_count": len(corrected_pass),
        "configured_gate_entered_after_fix": len(corrected_pass - legacy_pass),
        "configured_gate_exited_after_fix": len(legacy_pass - corrected_pass),
        "configured_gate_overlap": len(legacy_pass & corrected_pass),
        "configured_gate_flip_count": len(legacy_pass ^ corrected_pass),
    }
    return audit, summary


def global_emotion(
    current_rows: list[dict[str, Any]],
    prior_by_code: dict[str, dict[str, Any]],
    limit_by_code: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> tuple[float, list[dict[str, Any]], dict[str, Any]]:
    valid = [row for row in current_rows if row.get("close") is not None]
    up_count = sum(float(row.get("pct_chg") or 0) > 0 for row in valid)
    down_count = sum(float(row.get("pct_chg") or 0) < 0 for row in valid)
    flat_count = len(valid) - up_count - down_count
    limit_up_count = 0
    limit_down_count = 0
    break_board_count = 0
    for row in valid:
        code = normalize_code(row.get("ts_code"))
        limit = limit_by_code.get(code) or {}
        close = float(row.get("close") or 0)
        high = float(row.get("high") or close)
        up_limit = float(limit.get("up_limit") or 0)
        down_limit = float(limit.get("down_limit") or 0)
        if up_limit and close >= up_limit - 0.0001:
            limit_up_count += 1
        elif up_limit and high >= up_limit - 0.0001 and close < up_limit - 0.0001:
            break_board_count += 1
        if down_limit and close <= down_limit + 0.0001:
            limit_down_count += 1
    breadth_score = (
        up_count / (up_count + down_count) * 100 if up_count + down_count else 50
    )
    limit_up_score = score_fixed(
        limit_up_count, config["limit_up_min"], config["limit_up_max"]
    )
    limit_down_score = score_fixed(
        limit_down_count,
        config["limit_down_bad"],
        config["limit_down_good"],
        higher_is_better=True,
    )
    limit_structure_score = score_average([limit_up_score, limit_down_score])
    touched = limit_up_count + break_board_count
    break_rate = break_board_count / touched if touched else 0
    break_board_health = q4(100 * (1 - break_rate))
    current_amount = sum(float(row.get("amount") or 0) for row in valid)
    prior_amount = sum(
        float(prior_by_code.get(normalize_code(row.get("ts_code")), {}).get("amount") or 0)
        for row in valid
    )
    amount_change = (
        (current_amount / prior_amount - 1) * 100 if prior_amount > 0 else 0
    )
    amount_score = score_fixed(
        amount_change,
        config["amount_change_min_percent"],
        config["amount_change_max_percent"],
    )
    weights = config["components"]
    score = q4(
        breadth_score * float(weights["breadth_score"])
        + limit_structure_score * float(weights["limit_structure_score"])
        + break_board_health * float(weights["break_board_health"])
        + amount_score * float(weights["market_amount_change_score"])
    )
    details = [
        {
            "component": "breadth_score",
            "scope": "GLOBAL_MARKET",
            "raw_value": up_count / (up_count + down_count) if up_count + down_count else None,
            "score": q4(breadth_score),
            "weight": weights["breadth_score"],
            "variance": 0,
            "point_in_time": "PASS",
        },
        {
            "component": "limit_structure_score",
            "scope": "GLOBAL_MARKET",
            "raw_value": f"limit_up={limit_up_count};limit_down={limit_down_count}",
            "score": limit_structure_score,
            "weight": weights["limit_structure_score"],
            "variance": 0,
            "point_in_time": "PASS",
        },
        {
            "component": "break_board_health",
            "scope": "GLOBAL_MARKET",
            "raw_value": break_rate,
            "score": break_board_health,
            "weight": weights["break_board_health"],
            "variance": 0,
            "point_in_time": "PASS",
        },
        {
            "component": "market_amount_change_score",
            "scope": "GLOBAL_MARKET",
            "raw_value": amount_change,
            "score": amount_score,
            "weight": weights["market_amount_change_score"],
            "variance": 0,
            "point_in_time": "PASS",
        },
    ]
    summary = {
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "break_board_count": break_board_count,
        "break_board_rate": break_rate,
        "amount_change_percent": amount_change,
        "global_emotion_score": score,
        "cross_sectional_variance": 0,
    }
    return score, details, summary


def differentiated_emotion(
    codes: list[str],
    stock_by_code: dict[str, dict[str, Any]],
    current_by_code: dict[str, dict[str, Any]],
    prior_by_code: dict[str, dict[str, Any]],
    limit_by_code: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> tuple[dict[str, float], dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    sectors: dict[str, list[str]] = defaultdict(list)
    raw: dict[str, dict[str, Any]] = {}
    for code in codes:
        meta = stock_by_code.get(code) or {}
        industry = str(meta.get("industry") or "").strip() or "UNKNOWN"
        current = current_by_code.get(code) or {}
        prior = prior_by_code.get(code) or {}
        pct = float(current.get("pct_chg") or 0)
        amount = float(current.get("amount") or 0)
        prior_amount = float(prior.get("amount") or 0)
        raw[code] = {
            "industry": industry,
            "pct_chg": pct,
            "amount": amount,
            "prior_amount": prior_amount,
            "amount_change": (amount / prior_amount - 1) * 100 if prior_amount else None,
        }
        sectors[industry].append(code)
    sector_stats: dict[str, dict[str, Any]] = {}
    for industry, members in sectors.items():
        returns = [raw[code]["pct_chg"] for code in members]
        current_amount = sum(raw[code]["amount"] for code in members)
        prior_amount = sum(raw[code]["prior_amount"] for code in members)
        sector_stats[industry] = {
            "member_count": len(members),
            "return": statistics.fmean(returns) if returns else 0,
            "breadth": sum(value > 0 for value in returns) / len(returns) * 100
            if returns
            else 50,
            "amount_expansion": (current_amount / prior_amount - 1) * 100
            if prior_amount
            else None,
        }
    sector_return_scores = percentile_map(
        {key: float(value["return"]) for key, value in sector_stats.items()}
    )
    sector_expansion_scores = percentile_map(
        {
            key: float(value["amount_expansion"])
            for key, value in sector_stats.items()
            if value["amount_expansion"] is not None
        }
    )
    relative_sector = {
        code: raw[code]["pct_chg"] - sector_stats[raw[code]["industry"]]["return"]
        for code in codes
    }
    relative_sector_scores = percentile_map(relative_sector)
    full_a_scores = percentile_map({code: raw[code]["pct_chg"] for code in codes})
    within_sector_scores: dict[str, float] = {}
    for industry, members in sectors.items():
        local = percentile_map({code: raw[code]["pct_chg"] for code in members})
        within_sector_scores.update(local)
    weights = config["components"]
    scores: dict[str, float] = {}
    detail_by_code: dict[str, dict[str, Any]] = {}
    component_rows: list[dict[str, Any]] = []
    joined = 0
    for code in codes:
        industry = raw[code]["industry"]
        missing_industry = industry == "UNKNOWN"
        if not missing_industry:
            joined += 1
        current = current_by_code.get(code) or {}
        limit = limit_by_code.get(code) or {}
        close = float(current.get("close") or 0)
        high = float(current.get("high") or close)
        up_limit = float(limit.get("up_limit") or 0)
        down_limit = float(limit.get("down_limit") or 0)
        if down_limit and close <= down_limit + 0.0001:
            limit_state_score, limit_state = 0.0, "LIMIT_DOWN"
        elif up_limit and close >= up_limit - 0.0001:
            limit_state_score, limit_state = 100.0, "LIMIT_UP"
        elif up_limit and high >= up_limit - 0.0001 and close < up_limit - 0.0001:
            limit_state_score, limit_state = 25.0, "BREAK_BOARD"
        elif up_limit and close >= up_limit * 0.98:
            limit_state_score, limit_state = 75.0, "NEAR_LIMIT_UP"
        else:
            limit_state_score, limit_state = 50.0, "NORMAL"
        stats = sector_stats[industry]
        components = {
            "sector_return_score": 50.0
            if missing_industry
            else sector_return_scores[industry],
            "sector_breadth_score": 50.0 if missing_industry else q4(stats["breadth"]),
            "sector_amount_expansion_score": 50.0
            if missing_industry or industry not in sector_expansion_scores
            else sector_expansion_scores[industry],
            "relative_sector_score": 50.0
            if missing_industry
            else relative_sector_scores[code],
            "within_sector_rank_score": 50.0
            if missing_industry
            else within_sector_scores[code],
            "relative_full_a_score": full_a_scores[code],
            "stock_limit_state_score": limit_state_score,
        }
        score = q4(
            sum(float(components[name]) * float(weight) for name, weight in weights.items())
        )
        scores[code] = score
        detail_by_code[code] = {
            **components,
            "industry": industry,
            "limit_state": limit_state,
            "confidence_status": "DEGRADED_MISSING_INDUSTRY"
            if missing_industry
            else "COMPLETE",
        }
    for name, weight in weights.items():
        values = [detail_by_code[code][name] for code in codes]
        component_rows.append(
            {
                "component": name,
                "scope": "STOCK"
                if name
                in {
                    "relative_sector_score",
                    "within_sector_rank_score",
                    "relative_full_a_score",
                    "stock_limit_state_score",
                }
                else "SECTOR",
                "cache_available": True,
                "point_in_time": "PASS",
                "sample_count": len(values),
                "unique_score_count": len(set(values)),
                "variance": statistics.pvariance(values) if len(values) > 1 else 0,
                "weight": weight,
                "missing_count": sum(
                    detail_by_code[code]["confidence_status"] != "COMPLETE"
                    for code in codes
                )
                if "sector" in name
                else 0,
            }
        )
    summary = {
        "stock_count": len(codes),
        "industry_count": len(sectors),
        "industry_joined_count": joined,
        "industry_join_coverage": joined / len(codes) if codes else 0,
        "unknown_industry_count": len(codes) - joined,
        "unique_scores": len(set(scores.values())),
        "variance": statistics.pvariance(scores.values()) if len(scores) > 1 else 0,
    }
    return scores, detail_by_code, component_rows, summary


def build_versions(
    base_rows: list[dict[str, Any]],
    histories: dict[str, list[dict[str, Any]]],
    basic_by_code: dict[str, dict[str, Any]],
    flow_by_code: dict[str, dict[str, Any]],
    differentiated_scores: dict[str, float],
    global_emotion_score: float,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
    versions: dict[str, list[dict[str, Any]]] = {}
    detail: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    s0 = rank_version("S0_LEGACY", base_rows)
    versions["S0_LEGACY"] = s0
    s1_items: list[dict[str, Any]] = []
    s2_items: list[dict[str, Any]] = []
    for item in s0:
        code = normalize_code(item["stock_code"])
        latest = (histories.get(code) or [{}])[-1]
        raw_amount = float(latest.get("amount") or 0)
        amount_cny, _ = amount_to_cny(raw_amount)
        legacy_amount_score = score_fixed(raw_amount, 50_000_000, 300_000_000)
        corrected_amount_score = score_fixed(amount_cny, 50_000_000, 300_000_000)
        legacy_liquidity_score = score_fixed(raw_amount, 20_000_000, 200_000_000)
        corrected_liquidity_score = score_fixed(amount_cny, 20_000_000, 200_000_000)
        s1_capital = q4(
            (
                float(item["capital_score"]) * 4
                - legacy_amount_score
                + corrected_amount_score
            )
            / 4
        )
        s1_risk = q4(
            (
                float(item["risk_score"]) * 3
                - legacy_liquidity_score
                + corrected_liquidity_score
            )
            / 3
        )
        s1 = {
            **item,
            "capital_score": s1_capital,
            "risk_score": s1_risk,
            "emotion_score": float(item["emotion_score"]),
        }
        s1["total_score"] = weighted_total(s1)
        s1_items.append(s1)
        flow_available = code in flow_by_code
        legacy_volume_score = score_fixed(1.0, 0.8, 2.5) if flow_available else 50.0
        basic = basic_by_code.get(code) or {}
        raw_volume_ratio = basic.get("volume_ratio")
        if raw_volume_ratio in (None, ""):
            real_volume_score = 50.0
            volume_fallback = "RAW_VOLUME_RATIO_MISSING"
        else:
            real_volume_score = score_fixed(raw_volume_ratio, 0.8, 2.5)
            volume_fallback = None
        s2_capital = q4(
            (s1_capital * 4 - legacy_volume_score + real_volume_score) / 4
        )
        s2 = {**s1, "capital_score": s2_capital}
        s2["total_score"] = weighted_total(s2)
        s2_items.append(s2)
        detail["S1_AMOUNT_UNIT_FIX"][code] = {
            "raw_amount": raw_amount,
            "normalized_amount": amount_cny,
            "legacy_amount_score": legacy_amount_score,
            "corrected_amount_score": corrected_amount_score,
            "legacy_liquidity_score": legacy_liquidity_score,
            "corrected_liquidity_score": corrected_liquidity_score,
        }
        detail["S2_REAL_VOLUME_RATIO"][code] = {
            **detail["S1_AMOUNT_UNIT_FIX"][code],
            "raw_volume_ratio": raw_volume_ratio,
            "legacy_volume_ratio_score": legacy_volume_score,
            "real_volume_ratio_score": real_volume_score,
            "volume_ratio_fallback": volume_fallback,
        }
    versions["S1_AMOUNT_UNIT_FIX"] = rank_version("S1_AMOUNT_UNIT_FIX", s1_items)
    versions["S2_REAL_VOLUME_RATIO"] = rank_version("S2_REAL_VOLUME_RATIO", s2_items)
    s3_global = []
    s3_diff = []
    for item in versions["S2_REAL_VOLUME_RATIO"]:
        code = normalize_code(item["stock_code"])
        global_item = {**item, "emotion_score": global_emotion_score}
        global_item["total_score"] = weighted_total(global_item)
        s3_global.append(global_item)
        diff_item = {**item, "emotion_score": differentiated_scores[code]}
        diff_item["total_score"] = weighted_total(diff_item)
        s3_diff.append(diff_item)
    versions["S3_GLOBAL_EMOTION_REAL"] = rank_version(
        "S3_GLOBAL_EMOTION_REAL", s3_global
    )
    versions["S3_DIFFERENTIATED_EMOTION"] = rank_version(
        "S3_DIFFERENTIATED_EMOTION", s3_diff
    )
    s3_by_code = {
        normalize_code(item["stock_code"]): item
        for item in versions["S3_DIFFERENTIATED_EMOTION"]
    }
    percentile_groups: dict[str, dict[str, float]] = {}
    for factor in WEIGHTS:
        percentile_groups[factor] = percentile_map(
            {
                code: float(item[f"{factor}_score"])
                for code, item in s3_by_code.items()
            }
        )
    s4_items = []
    for code, item in s3_by_code.items():
        converted = {
            **item,
            **{
                f"{factor}_score": percentile_groups[factor][code]
                for factor in WEIGHTS
            },
        }
        converted["total_score"] = weighted_total(converted)
        s4_items.append(converted)
    versions["S4_PERCENTILE_NORMALIZATION"] = rank_version(
        "S4_PERCENTILE_NORMALIZATION", s4_items
    )
    return versions, detail


def version_summary(
    version: str,
    ranked: list[dict[str, Any]],
    stock_by_code: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    total = distribution(float(row["total_score"]) for row in ranked)
    top100 = ranked[:100]
    factor_stats = {
        factor: {
            "mean": statistics.fmean(float(row[f"{factor}_score"]) for row in ranked),
            "variance": statistics.pvariance(
                float(row[f"{factor}_score"]) for row in ranked
            ),
            "unique": len(set(float(row[f"{factor}_score"]) for row in ranked)),
            "equal_50": sum(
                math.isclose(float(row[f"{factor}_score"]), 50, abs_tol=0.00005)
                for row in ranked
            ),
        }
        for factor in WEIGHTS
    }
    sector_counter = Counter(
        str((stock_by_code.get(normalize_code(row["stock_code"])) or {}).get("industry") or "UNKNOWN")
        for row in top100
    )
    hhi = sum((count / len(top100)) ** 2 for count in sector_counter.values())
    circ_mv_values = [
        float(
            (stock_by_code.get(normalize_code(row["stock_code"])) or {}).get(
                "circ_mv", 0
            )
            or 0
        )
        for row in top100
    ]
    return {
        "version": version,
        "universe_mode": "FIXED",
        "universe_count": len(ranked),
        "score_min": total["min"],
        "score_max": total["max"],
        "score_mean": total["mean"],
        "score_median": total["median"],
        "score_std": total["std"],
        "unique_total_scores": total["unique_value_count"],
        "total_equal_50": total["count_equal_50"],
        "top100_min": min(float(row["total_score"]) for row in top100),
        "top100_max": max(float(row["total_score"]) for row in top100),
        "sector_hhi_top100": hhi,
        "top100_sector_count": len(sector_counter),
        "top100_circ_mv_mean_10k_cny": statistics.fmean(circ_mv_values)
        if circ_mv_values
        else 0,
        "factor_stats": factor_stats,
        "top20": [normalize_code(row["stock_code"]) for row in ranked[:20]],
        "top50": [normalize_code(row["stock_code"]) for row in ranked[:50]],
        "top100": [normalize_code(row["stock_code"]) for row in top100],
    }


def compare_versions(
    base: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    base_summary: dict[str, Any],
    candidate_summary: dict[str, Any],
) -> dict[str, Any]:
    base_rank = {normalize_code(row["stock_code"]): int(row["rank"]) for row in base}
    candidate_rank = {
        normalize_code(row["stock_code"]): int(row["rank"]) for row in candidate
    }
    common = sorted(set(base_rank) & set(candidate_rank))
    left = [base_rank[code] for code in common]
    right = [candidate_rank[code] for code in common]
    deltas = [abs(a - b) for a, b in zip(left, right)]
    base_top100 = set(base_summary["top100"])
    candidate_top100 = set(candidate_summary["top100"])

    def overlap(size: int) -> tuple[int, float]:
        a = {normalize_code(row["stock_code"]) for row in base[:size]}
        b = {normalize_code(row["stock_code"]) for row in candidate[:size]}
        count = len(a & b)
        return count, count / size

    top20_count, top20_ratio = overlap(20)
    top50_count, top50_ratio = overlap(50)
    top100_count, top100_ratio = overlap(100)
    return {
        "version": candidate[0]["version"],
        "spearman": float(spearmanr(left, right).statistic) if common else None,
        "kendall": float(kendalltau(left, right).statistic) if common else None,
        "mean_rank_delta": statistics.fmean(deltas) if deltas else None,
        "median_rank_delta": statistics.median(deltas) if deltas else None,
        "max_rank_delta": max(deltas) if deltas else None,
        "top20_overlap_count": top20_count,
        "top20_overlap": top20_ratio,
        "top50_overlap_count": top50_count,
        "top50_overlap": top50_ratio,
        "top100_overlap_count": top100_count,
        "top100_overlap": top100_ratio,
        "entered_top100": sorted(candidate_top100 - base_top100),
        "exited_top100": sorted(base_top100 - candidate_top100),
        "top100_flip_count": len(candidate_top100 ^ base_top100),
        "sector_concentration_delta": candidate_summary["sector_hhi_top100"]
        - base_summary["sector_hhi_top100"],
    }


def fallback_audit(
    codes: list[str],
    histories: dict[str, list[dict[str, Any]]],
    basic_by_code: dict[str, dict[str, Any]],
    flow_by_code: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result: list[dict[str, Any]] = []
    capital_stocks: set[str] = set()
    fixed_default = 0
    neutral_50 = 0
    zero_fallback = 0
    clip_saturation = 0
    data_quality_stocks: set[str] = set()
    subfactor_counts = Counter()
    for code in codes:
        latest = (histories.get(code) or [{}])[-1]
        raw_amount = float(latest.get("amount") or 0)
        amount_score = score_fixed(raw_amount, 50_000_000, 300_000_000)
        if amount_score in {0, 100}:
            clip_saturation += 1
        data_quality_stocks.add(code)
        result.append(
            {
                "stock_code": code,
                "factor_group": "capital",
                "factor_name": "amount_score",
                "missing_reason": "DAILY_AMOUNT_UNIT_MISMATCH",
                "fallback_type": "DATA_QUALITY_UNIT_ERROR_PASSTHROUGH",
                "fallback_value": amount_score,
                "effective_weight": 0.25,
                "confidence_status": "INVALID_UNIT",
            }
        )
        flow = flow_by_code.get(code)
        if flow:
            volume_score = score_fixed(1.0, 0.8, 2.5)
            result.append(
                {
                    "stock_code": code,
                    "factor_group": "capital",
                    "factor_name": "volume_ratio_score",
                    "missing_reason": "DAILY_BASIC_VOLUME_RATIO_IGNORED",
                    "fallback_type": "HARD_CODED_DEFAULT",
                    "fallback_value": 1.0,
                    "effective_weight": 0.25,
                    "confidence_status": "DEGRADED",
                }
            )
            fixed_default += 1
            subfactor_counts["capital.volume_ratio_score"] += 1
            capital_stocks.add(code)
            turnover_score = score_fixed(
                (basic_by_code.get(code) or {}).get("turnover_rate"), 0.5, 8
            )
            inflow_score = score_fixed(
                float(flow.get("net_mf_amount") or 0) * 10000,
                -5_000_000,
                20_000_000,
            )
            clip_saturation += int(turnover_score in {0, 100}) + int(
                inflow_score in {0, 100}
            )
        else:
            for name in (
                "volume_ratio_score",
                "turnover_score",
                "main_inflow_score",
            ):
                result.append(
                    {
                        "stock_code": code,
                        "factor_group": "capital",
                        "factor_name": name,
                        "missing_reason": "MONEYFLOW_NOT_COVERED",
                        "fallback_type": "MISSING_NEUTRAL_FALLBACK",
                        "fallback_value": 50,
                        "effective_weight": 0.25,
                        "confidence_status": "DEGRADED",
                    }
                )
                neutral_50 += 1
                subfactor_counts[f"capital.{name}"] += 1
            capital_stocks.add(code)
        for name, raw_value, score in (
            ("limit_up_environment_score", 0, 0),
            ("limit_down_control_score", 0, 100),
            ("market_heat_score", 50, 50),
        ):
            result.append(
                {
                    "stock_code": code,
                    "factor_group": "emotion",
                    "factor_name": name,
                    "missing_reason": "TUSHARE_MARKET_EMOTION_PLACEHOLDER",
                    "fallback_type": "HARD_CODED_DEFAULT",
                    "fallback_value": raw_value,
                    "score": score,
                    "effective_weight": 0.20,
                    "confidence_status": "INVALID_PLACEHOLDER",
                }
            )
            fixed_default += 1
            subfactor_counts[f"emotion.{name}"] += 1
    summary = {
        "capital_fallback_stock_count": len(capital_stocks),
        "capital_full_fallback_count": 0,
        "capital_fallback_cell_count": sum(
            row["factor_group"] == "capital"
            and row["fallback_type"]
            in {"HARD_CODED_DEFAULT", "MISSING_NEUTRAL_FALLBACK"}
            for row in result
        ),
        "stocks_over_50_percent_capital_missing": sum(
            code not in flow_by_code for code in codes
        ),
        "subfactor_fallback_counts": dict(subfactor_counts),
        "neutral_50_fallback_count": neutral_50,
        "zero_score_fallback_count": zero_fallback,
        "fixed_default_cell_count": fixed_default,
        "clip_saturation_cell_count": clip_saturation,
        "data_quality_abnormal_stock_count": len(data_quality_stocks),
    }
    return result, summary


def moneyflow_bias(
    ranked: list[dict[str, Any]],
    flow_by_code: dict[str, dict[str, Any]],
    stock_by_code: dict[str, dict[str, Any]],
    histories: dict[str, list[dict[str, Any]]],
    basic_by_code: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rank_by_code = {normalize_code(row["stock_code"]): row for row in ranked}
    group_codes = {
        "MONEYFLOW_AVAILABLE": [
            code for code in rank_by_code if code in flow_by_code
        ],
        "MONEYFLOW_MISSING": [
            code for code in rank_by_code if code not in flow_by_code
        ],
    }
    output: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    asof = date.fromisoformat(TRADE_DATE)
    for group, codes in group_codes.items():
        capital = [float(rank_by_code[code]["capital_score"]) for code in codes]
        total = [float(rank_by_code[code]["total_score"]) for code in codes]
        amounts = [
            amount_to_cny(float((histories.get(code) or [{}])[-1].get("amount") or 0))[0]
            for code in codes
        ]
        turnover = [
            float((basic_by_code.get(code) or {}).get("turnover_rate") or 0)
            for code in codes
        ]
        listing_days = []
        for code in codes:
            text = str((stock_by_code.get(code) or {}).get("list_date") or "")
            if len(text) == 8 and text.isdigit():
                listing_days.append(
                    (asof - date(int(text[:4]), int(text[4:6]), int(text[6:]))).days
                )
        top20 = sum(int(rank_by_code[code]["rank"]) <= 20 for code in codes)
        top100 = sum(int(rank_by_code[code]["rank"]) <= 100 for code in codes)
        top500 = sum(int(rank_by_code[code]["rank"]) <= 500 for code in codes)
        suspended = sum(
            not histories.get(code)
            or str(histories[code][-1].get("trade_date")) != TRADE_KEY
            for code in codes
        )
        sector_counts = Counter(
            str((stock_by_code.get(code) or {}).get("industry") or "UNKNOWN")
            for code in codes
        )
        circ_mv = [
            float((basic_by_code.get(code) or {}).get("circ_mv") or 0)
            for code in codes
        ]
        summary = {
            "record_type": "SUMMARY",
            "group": group,
            "sample_count": len(codes),
            "avg_capital_score": statistics.fmean(capital) if capital else None,
            "median_capital_score": statistics.median(capital) if capital else None,
            "avg_quant_score": statistics.fmean(total) if total else None,
            "top20_count": top20,
            "top20_entry_rate": top20 / len(codes) if codes else 0,
            "top100_count": top100,
            "top100_entry_rate": top100 / len(codes) if codes else 0,
            "top500_count": top500,
            "top500_entry_rate": top500 / len(codes) if codes else 0,
            "avg_listing_days": statistics.fmean(listing_days)
            if listing_days
            else None,
            "new_stock_ratio": sum(value < 60 for value in listing_days)
            / len(listing_days)
            if listing_days
            else None,
            "suspended_ratio": suspended / len(codes) if codes else 0,
            "st_or_abnormal_ratio": 0,
            "avg_amount_cny": statistics.fmean(amounts) if amounts else None,
            "avg_turnover_rate": statistics.fmean(turnover) if turnover else None,
            "avg_circ_mv_10k_cny": statistics.fmean(circ_mv) if circ_mv else None,
            "industry_count": len(sector_counts),
        }
        output.append(summary)
        summaries[group] = summary
        for industry, count in sector_counts.most_common():
            output.append(
                {
                    "record_type": "INDUSTRY_DISTRIBUTION",
                    "group": group,
                    "industry": industry,
                    "industry_count": count,
                    "industry_ratio": count / len(codes) if codes else 0,
                }
            )
    available = summaries["MONEYFLOW_AVAILABLE"]
    missing = summaries["MONEYFLOW_MISSING"]
    delta = float(missing["avg_capital_score"]) - float(available["avg_capital_score"])
    if missing["sample_count"] < 30:
        finding = "INSUFFICIENT_SAMPLE"
    elif delta > 2:
        finding = "SYSTEMATIC_REWARD"
    elif delta < -2:
        finding = "SYSTEMATIC_PENALTY"
    else:
        finding = "APPROXIMATELY_NEUTRAL"
    return output, {
        "available": available["sample_count"],
        "missing": missing["sample_count"],
        "capital_score_delta_missing_minus_available": delta,
        "finding": finding,
    }


def amount_impact_modules(
    base: list[dict[str, Any]],
    s1: list[dict[str, Any]],
    universe_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    base_by = {normalize_code(row["stock_code"]): row for row in base}
    s1_by = {normalize_code(row["stock_code"]): row for row in s1}
    capital_changed = sum(
        not math.isclose(
            float(base_by[code]["capital_score"]),
            float(s1_by[code]["capital_score"]),
            abs_tol=0.00005,
        )
        for code in base_by
    )
    risk_changed = sum(
        not math.isclose(
            float(base_by[code]["risk_score"]),
            float(s1_by[code]["risk_score"]),
            abs_tol=0.00005,
        )
        for code in base_by
    )
    top100_before = {normalize_code(row["stock_code"]) for row in base[:100]}
    top100_after = {normalize_code(row["stock_code"]) for row in s1[:100]}
    common = len(base_by)
    return [
        {
            "module": "Quant Capital",
            "file": "quant/factors.py",
            "function": "CapitalFactorCalculator.calculate",
            "raw_field": "daily.amount",
            "raw_unit": "THOUSAND_CNY",
            "expected_unit": "CNY",
            "current_conversion": "x1",
            "expected_conversion": "x1000",
            "threshold": "50m-300m CNY",
            "affected_stock_count": capital_changed,
            "affected_gate_count": 0,
            "affected_rank_count": len(top100_before ^ top100_after),
            "impact_type": "SCORE_ERROR",
            "severity": "CRITICAL",
        },
        {
            "module": "Quant Risk Liquidity",
            "file": "quant/factors.py",
            "function": "RiskFactorCalculator.calculate",
            "raw_field": "daily.amount",
            "raw_unit": "THOUSAND_CNY",
            "expected_unit": "CNY",
            "current_conversion": "x1",
            "expected_conversion": "x1000",
            "threshold": "20m-200m CNY",
            "affected_stock_count": risk_changed,
            "affected_gate_count": 0,
            "affected_rank_count": len(top100_before ^ top100_after),
            "impact_type": "SCORE_ERROR",
            "severity": "CRITICAL",
        },
        {
            "module": "Configured Universe Amount Gate",
            "file": "config/quant_factor.yaml + scripts/run_real_quant_top500.py",
            "function": "_filter_stock_universe",
            "raw_field": "daily.amount",
            "raw_unit": "THOUSAND_CNY",
            "expected_unit": "CNY",
            "current_conversion": "GATE_NOT_WIRED",
            "expected_conversion": "x1000 before threshold if enabled",
            "threshold": "50m CNY",
            "affected_stock_count": universe_summary[
                "configured_gate_flip_count"
            ],
            "affected_gate_count": universe_summary[
                "configured_gate_flip_count"
            ],
            "affected_rank_count": 0,
            "impact_type": "UNWIRED_HARD_GATE_COUNTERFACTUAL",
            "severity": "HIGH",
        },
        {
            "module": "Order Price VWAP",
            "file": "order_price/price_level_calculator.py",
            "function": "calculate_vwap",
            "raw_field": "amount / volume",
            "raw_unit": "THOUSAND_CNY / LOT",
            "expected_unit": "CNY / SHARE",
            "current_conversion": "x1 / x1",
            "expected_conversion": "amount x1000; volume x100",
            "threshold": "price calculation",
            "affected_stock_count": common,
            "affected_gate_count": 0,
            "affected_rank_count": 0,
            "impact_type": "PRICE_CALCULATION_ERROR",
            "severity": "CRITICAL",
        },
        {
            "module": "Order Fill Probability",
            "file": "order_price/execution_probability.py",
            "function": "estimate_fill_probability",
            "raw_field": "amount",
            "raw_unit": "THOUSAND_CNY on Tushare path",
            "expected_unit": "CNY",
            "current_conversion": "x1",
            "expected_conversion": "x1000",
            "threshold": "1bn CNY saturation",
            "affected_stock_count": common,
            "affected_gate_count": 0,
            "affected_rank_count": 0,
            "impact_type": "EXECUTION_PROBABILITY_ERROR",
            "severity": "HIGH",
        },
        {
            "module": "Position Capacity",
            "file": "model_validation/service.py + position_sizing/engine.py",
            "function": "_size_positions / evaluate",
            "raw_field": "average_daily_amount",
            "raw_unit": "THOUSAND_CNY on Tushare path",
            "expected_unit": "CNY",
            "current_conversion": "x1",
            "expected_conversion": "x1000",
            "threshold": "1% liquidity participation",
            "affected_stock_count": common,
            "affected_gate_count": common,
            "affected_rank_count": 0,
            "impact_type": "CAPACITY_CONSTRAINT_ERROR",
            "severity": "CRITICAL",
        },
        {
            "module": "Midday Radar",
            "file": "midday/full_a_data.py + midday/asof.py",
            "function": "snapshot amount progress",
            "raw_field": "iFinD minute amount",
            "raw_unit": "provider-specific CNY",
            "expected_unit": "CNY",
            "current_conversion": "provider adapter",
            "expected_conversion": "no Tushare daily conversion",
            "threshold": "intraday progress",
            "affected_stock_count": 0,
            "affected_gate_count": 0,
            "affected_rank_count": 0,
            "impact_type": "SEPARATE_SOURCE_NOT_AFFECTED",
            "severity": "NONE",
        },
        {
            "module": "Post-close Minute Liquidity",
            "file": "post_close/scoring.py",
            "function": "score minute completeness/liquidity",
            "raw_field": "minute.amount",
            "raw_unit": "iFinD persisted CNY",
            "expected_unit": "CNY",
            "current_conversion": "provider adapter",
            "expected_conversion": "no Tushare daily conversion",
            "threshold": "minute completeness",
            "affected_stock_count": 0,
            "affected_gate_count": 0,
            "affected_rank_count": 0,
            "impact_type": "SEPARATE_SOURCE_NOT_AFFECTED",
            "severity": "NONE",
        },
    ]


def capital_collision_lineage(
    base: list[dict[str, Any]],
    histories: dict[str, list[dict[str, Any]]],
    basic_by_code: dict[str, dict[str, Any]],
    flow_by_code: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    groups = (52.9412, 37.5)
    by_score: dict[float, list[str]] = defaultdict(list)
    for item in base:
        score = round(float(item["capital_score"]), 4)
        if score in groups:
            by_score[score].append(normalize_code(item["stock_code"]))
    for group_score in groups:
        codes = by_score[group_score]
        representative = codes[0] if codes else ""
        latest = (histories.get(representative) or [{}])[-1]
        flow = flow_by_code.get(representative)
        basic = basic_by_code.get(representative) or {}
        if group_score == 52.9412:
            components = (
                (
                    "amount_score",
                    float(latest.get("amount") or 0),
                    float(latest.get("amount") or 0),
                    0,
                    "DATA_QUALITY_UNIT_ERROR",
                    "LOWER_CLIP",
                ),
                (
                    "volume_ratio_score",
                    1.0,
                    1.0,
                    11.7647,
                    "HARD_CODED_DEFAULT",
                    "NONE",
                ),
                (
                    "turnover_score",
                    float(basic.get("turnover_rate") or 0),
                    float(basic.get("turnover_rate") or 0),
                    100,
                    "NONE",
                    "UPPER_CLIP",
                ),
                (
                    "main_inflow_score",
                    float((flow or {}).get("net_mf_amount") or 0) * 10000,
                    float((flow or {}).get("net_mf_amount") or 0) * 10000,
                    100,
                    "NONE",
                    "UPPER_CLIP",
                ),
            )
        else:
            components = (
                (
                    "amount_score",
                    float(latest.get("amount") or 0),
                    float(latest.get("amount") or 0),
                    0,
                    "DATA_QUALITY_UNIT_ERROR",
                    "LOWER_CLIP",
                ),
                ("volume_ratio_score", None, None, 50, "MISSING_NEUTRAL_FALLBACK", "NONE"),
                ("turnover_score", None, None, 50, "MISSING_NEUTRAL_FALLBACK", "NONE"),
                ("main_inflow_score", None, None, 50, "MISSING_NEUTRAL_FALLBACK", "NONE"),
            )
        for name, raw_value, normalized, score, fallback, clip in components:
            result.append(
                {
                    "capital_group_score": group_score,
                    "group_stock_count": len(codes),
                    "representative_stock": representative,
                    "factor_name": name,
                    "raw_value": raw_value,
                    "normalized_value": normalized,
                    "score": score,
                    "fallback_type": fallback,
                    "clip_status": clip,
                    "weight": 0.25,
                    "contribution": score / 4,
                }
            )
    return result


def workbook_hash_audit(
    workbook: Path, recorded_hash: str | None
) -> dict[str, Any]:
    with zipfile.ZipFile(workbook) as archive:
        names = archive.namelist()
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8", "replace")
        sheet_names = re.findall(r'<sheet[^>]+name="([^"]+)"', workbook_xml)
        sheet_xml = [
            archive.read(name).decode("utf-8", "replace")
            for name in names
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        ]
        content_material = []
        formulas = []
        style_refs = []
        for text in sheet_xml:
            content_material.append(re.sub(r'\ss="\d+"', "", text))
            formulas.extend(re.findall(r"<f[^>]*>(.*?)</f>", text, flags=re.S))
            style_refs.extend(re.findall(r'\ss="(\d+)"', text))
        if "xl/sharedStrings.xml" in names:
            content_material.append(
                archive.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            )
        styles = (
            archive.read("xl/styles.xml") if "xl/styles.xml" in names else b""
        )
        app_properties = (
            archive.read("docProps/app.xml").decode("utf-8", "replace")
            if "docProps/app.xml" in names
            else ""
        )
        application_match = re.search(
            r"<Application>(.*?)</Application>", app_properties, flags=re.S
        )
        app_version_match = re.search(
            r"<AppVersion>(.*?)</AppVersion>", app_properties, flags=re.S
        )
        generator_parts = [
            match.group(1).strip()
            for match in (application_match, app_version_match)
            if match and match.group(1).strip()
        ]
    current_hash = file_sha256(workbook)
    stat = workbook.stat()
    diagnosis = (
        "HASH_RECORD_ERROR"
        if not recorded_hash
        else "CONTENT_UNCHANGED_STYLE_CHANGED"
        if current_hash == recorded_hash
        else "ORIGINAL_OVERWRITTEN"
    )
    return {
        "path": str(workbook),
        "original_recorded_sha256": recorded_hash,
        "current_sha256": current_hash,
        "file_size": stat.st_size,
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime, timezone.utc
        ).isoformat(),
        "sheet_names": sheet_names,
        "cell_content_hash": hashlib.sha256(
            "\n".join(content_material).encode("utf-8")
        ).hexdigest(),
        "formula_hash": hashlib.sha256(
            "\n".join(formulas).encode("utf-8")
        ).hexdigest(),
        "style_hash": hashlib.sha256(
            styles + "\n".join(style_refs).encode("utf-8")
        ).hexdigest(),
        "generator_version": " ".join(generator_parts) or "UNKNOWN",
        "data_source_run_id": BASE_RUN_ID,
        "recorded_file_available": False,
        "recorded_hash_copy_found": False,
        "content_change_vs_recorded": "UNKNOWN_ORIGINAL_BYTES_UNAVAILABLE",
        "style_change_vs_recorded": "UNKNOWN_ORIGINAL_BYTES_UNAVAILABLE",
        "diagnosis": diagnosis,
    }


def factor_detail_persistence_audit(
    connection: sqlite3.Connection, report: dict[str, Any]
) -> list[dict[str, Any]]:
    report_detail_codes = {
        normalize_code(row["stock_code"])
        for row in report.get("top_stocks") or []
        if row.get("factor_detail")
    }
    base_codes = [
        normalize_code(row["stock_code"]) for row in report["all_scored_stocks"]
    ]
    db_score_count = sum(
        connection.execute(
            "SELECT 1 FROM stock_factor_score "
            "WHERE stock_code=? AND date=? AND factor_version=? LIMIT 1",
            (code, TRADE_DATE, BASE_FACTOR_VERSION),
        ).fetchone()
        is not None
        for code in base_codes
    )
    db_detail_count = sum(
        connection.execute(
            "SELECT 1 FROM stock_factor_detail "
            "WHERE stock_code=? AND date=? AND factor_version=? LIMIT 1",
            (code, TRADE_DATE, BASE_FACTOR_VERSION),
        ).fetchone()
        is not None
        for code in base_codes
    )
    return [
        {
            "expected_detail_stock_count": len(report["all_scored_stocks"]),
            "actual_report_detail_stock_count": len(report_detail_codes),
            "actual_database_score_stock_count": db_score_count,
            "actual_database_detail_stock_count": db_detail_count,
            "missing_detail_stock_count": len(report["all_scored_stocks"])
            - len(report_detail_codes),
            "missing_detail_reason": "report top_stocks is capped at Top Q=5000 while all_scored_stocks has group scores only",
            "save_scope": "REPORT_TOP_Q",
            "transaction_boundary": "quant.persistence.save_factor_scores commits supplied ranking result only",
            "batching_limit": 5000,
            "exporter_scope": "all_scored group scores; top_stocks detailed factors",
            "database_error": "NONE_OBSERVED",
            "intentional_or_bug": "PERSISTENCE_DESIGN_GAP",
        }
    ]


def shadow_details(
    version: str,
    ranked: list[dict[str, Any]],
    version_detail: dict[str, dict[str, Any]],
    diff_detail: dict[str, dict[str, Any]],
    global_score: float,
    config: dict[str, Any],
    run_meta: dict[str, str],
) -> list[tuple]:
    created_at = datetime.now(timezone.utc).isoformat()
    output: list[tuple] = []
    group_weights = {key: float(value) for key, value in WEIGHTS.items()}
    for item in ranked:
        code = normalize_code(item["stock_code"])
        for factor, weight in group_weights.items():
            score = float(item[f"{factor}_score"])
            output.append(
                (
                    run_meta["shadow_run_id"],
                    code,
                    factor,
                    f"{factor}_group_score",
                    score,
                    score,
                    score,
                    weight,
                    q4(score * weight),
                    None,
                    None,
                    "HISTORICAL_GROUP_SCORE"
                    if version == "S0_LEGACY"
                    else "SHADOW_GROUP_SCORE",
                    run_meta["factor_version"],
                    BASE_INPUT_HASH,
                    run_meta["universe_snapshot_id"],
                    created_at,
                )
            )
        data = version_detail.get(code) or {}
        if version != "S0_LEGACY":
            for name in (
                "legacy_amount_score",
                "corrected_amount_score",
                "legacy_liquidity_score",
                "corrected_liquidity_score",
                "legacy_volume_ratio_score",
                "real_volume_ratio_score",
            ):
                if name not in data:
                    continue
                factor = "risk" if "liquidity" in name else "capital"
                score = float(data[name])
                raw_key = (
                    "normalized_amount"
                    if "corrected" in name
                    else "raw_volume_ratio"
                    if "volume" in name and "real" in name
                    else "raw_amount"
                )
                output.append(
                    (
                        run_meta["shadow_run_id"],
                        code,
                        factor,
                        name,
                        data.get(raw_key),
                        score,
                        score,
                        group_weights[factor],
                        q4(score * group_weights[factor]),
                        data.get("volume_ratio_fallback"),
                        data.get("volume_ratio_fallback"),
                        "CLIP_SCORE",
                        run_meta["factor_version"],
                        BASE_INPUT_HASH,
                        run_meta["universe_snapshot_id"],
                        created_at,
                    )
                )
        if version == "S3_GLOBAL_EMOTION_REAL":
            output.append(
                (
                    run_meta["shadow_run_id"],
                    code,
                    "emotion",
                    "global_market_emotion",
                    global_score,
                    global_score,
                    global_score,
                    group_weights["emotion"],
                    q4(global_score * group_weights["emotion"]),
                    None,
                    None,
                    "GLOBAL_MARKET_VALUE",
                    run_meta["factor_version"],
                    BASE_INPUT_HASH,
                    run_meta["universe_snapshot_id"],
                    created_at,
                )
            )
        if version in {
            "S3_DIFFERENTIATED_EMOTION",
            "S4_PERCENTILE_NORMALIZATION",
        }:
            components = config["differentiated_emotion"]["components"]
            for name, weight in components.items():
                score = float((diff_detail.get(code) or {}).get(name, 50))
                output.append(
                    (
                        run_meta["shadow_run_id"],
                        code,
                        "emotion",
                        name,
                        score,
                        score,
                        score,
                        float(weight),
                        q4(score * float(weight)),
                        None,
                        "MISSING_INDUSTRY"
                        if (diff_detail.get(code) or {}).get("industry") == "UNKNOWN"
                        else None,
                        config["percentile_normalization"]["subfactor_rules"].get(
                            name, "STOCK_LEVEL_VALUE"
                        ),
                        run_meta["factor_version"],
                        BASE_INPUT_HASH,
                        run_meta["universe_snapshot_id"],
                        created_at,
                    )
                )
    return output


def initialize_shadow_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SHADOW_MIGRATION.read_text(encoding="utf-8"))
    connection.commit()
    return connection


def persist_shadow(
    connection: sqlite3.Connection,
    versions: dict[str, list[dict[str, Any]]],
    summaries: dict[str, dict[str, Any]],
    comparisons: dict[str, dict[str, Any]],
    universe_audit: list[dict[str, Any]],
    version_detail: dict[str, dict[str, dict[str, Any]]],
    diff_detail: dict[str, dict[str, Any]],
    global_score: float,
    config: dict[str, Any],
    config_hash: str,
    logic_hash: str,
) -> dict[str, dict[str, str]]:
    created_at = datetime.now(timezone.utc).isoformat()
    metadata: dict[str, dict[str, str]] = {}
    for version, ranked in versions.items():
        universe_snapshot_id = "sha256:" + canonical_hash(
            sorted(normalize_code(row["stock_code"]) for row in ranked)
        )
        factor_version = f"shadow_quant_v1::{version}"
        payload_hash = canonical_hash(
            {
                "version": version,
                "input_hash": BASE_INPUT_HASH,
                "config_hash": config_hash,
                "logic_hash": logic_hash,
                "scores": [
                    (
                        normalize_code(row["stock_code"]),
                        row["rank"],
                        row["total_score"],
                    )
                    for row in ranked
                ],
            }
        )
        shadow_run_id = f"shadow-{version.lower()}-{payload_hash[:16]}"
        existing = connection.execute(
            "SELECT immutable_payload_hash FROM quant_shadow_run WHERE shadow_run_id=?",
            (shadow_run_id,),
        ).fetchone()
        if existing:
            if existing[0] != payload_hash:
                raise RuntimeError("IMMUTABLE_SHADOW_RUN_HASH_CONFLICT")
            metadata[version] = {
                "shadow_run_id": shadow_run_id,
                "factor_version": factor_version,
                "universe_snapshot_id": universe_snapshot_id,
                "immutable_payload_hash": payload_hash,
                "persistence": "REUSED_IDENTICAL_IMMUTABLE_RUN",
            }
            continue
        run_meta = {
            "shadow_run_id": shadow_run_id,
            "factor_version": factor_version,
            "universe_snapshot_id": universe_snapshot_id,
        }
        connection.execute(
            "INSERT INTO quant_shadow_run VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                shadow_run_id,
                BASE_RUN_ID,
                TRADE_DATE,
                factor_version,
                BASE_INPUT_HASH,
                config_hash,
                universe_snapshot_id,
                "PER_STOCK_FIXED_RANGE"
                if version != "S4_PERCENTILE_NORMALIZATION"
                else "FULL_A_FACTOR_GROUP_PERCENTILE",
                "FIXED",
                len(ranked),
                payload_hash,
                created_at,
            ),
        )
        connection.executemany(
            "INSERT INTO quant_shadow_score VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    shadow_run_id,
                    BASE_RUN_ID,
                    TRADE_DATE,
                    normalize_code(row["stock_code"]),
                    factor_version,
                    BASE_INPUT_HASH,
                    config_hash,
                    universe_snapshot_id,
                    "PER_STOCK_FIXED_RANGE"
                    if version != "S4_PERCENTILE_NORMALIZATION"
                    else "FULL_A_FACTOR_GROUP_PERCENTILE",
                    row["technical_score"],
                    row["capital_score"],
                    row["emotion_score"],
                    row["momentum_score"],
                    row["risk_score"],
                    row["total_score"],
                    row["rank"],
                    created_at,
                )
                for row in ranked
            ],
        )
        details = shadow_details(
            version,
            ranked,
            version_detail.get(version) or version_detail.get("S2_REAL_VOLUME_RATIO", {}),
            diff_detail,
            global_score,
            config,
            run_meta,
        )
        connection.executemany(
            "INSERT INTO quant_shadow_factor_detail VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            details,
        )
        connection.executemany(
            "INSERT INTO quant_data_quality_audit VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    shadow_run_id,
                    normalize_code(row["stock_code"]),
                    "capital/risk",
                    "daily.amount",
                    "HISTORICAL_UNIT_ERROR"
                    if version == "S0_LEGACY"
                    else "UNIT_CORRECTED_IN_SHADOW",
                    "Tushare daily.amount is THOUSAND_CNY; formal adapter passed x1",
                    "INVALID"
                    if version == "S0_LEGACY"
                    else "SHADOW_CORRECTED",
                    created_at,
                )
                for row in ranked
            ],
        )
        connection.executemany(
            "INSERT INTO quant_shadow_universe_audit VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    shadow_run_id,
                    row["stock_code"],
                    row["stock_name"],
                    row["exclusion_stage"],
                    row["exclusion_reason"],
                    row["raw_amount"],
                    row["normalized_amount"],
                    row["threshold"],
                    int(bool(row["unit_fix_changes_result"])),
                    row["final_status"],
                    created_at,
                )
                for row in universe_audit
            ],
        )
        comparison = comparisons[version]
        connection.execute(
            "INSERT INTO quant_shadow_comparison VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                shadow_run_id,
                BASE_RUN_ID,
                comparison["spearman"],
                comparison["kendall"],
                comparison["mean_rank_delta"],
                comparison["median_rank_delta"],
                comparison["max_rank_delta"],
                comparison["top20_overlap"],
                comparison["top50_overlap"],
                comparison["top100_overlap"],
                comparison["top100_flip_count"],
                comparison["sector_concentration_delta"],
                created_at,
            ),
        )
        connection.commit()
        metadata[version] = {
            **run_meta,
            "immutable_payload_hash": payload_hash,
            "persistence": "INSERTED_IMMUTABLE_RUN",
        }
    return metadata


def markdown_report(report: dict[str, Any]) -> str:
    versions = report["version_summaries"]
    comparisons = report["comparisons"]
    return "\n".join(
        [
            "# Quant Factor Immutable Shadow Counterfactual V1",
            "",
            f"- Base run: `{BASE_RUN_ID}`",
            f"- Trade date: `{TRADE_DATE}`",
            f"- Input hash: `{BASE_INPUT_HASH}`",
            f"- Final status: `{report['final_status']}`",
            "",
            "## Confirmed Bug Fixes",
            "",
            "- `daily.amount`必须由千元乘1000转换为人民币元。",
            "- `daily_basic.volume_ratio`已有缓存值，正式路径却固定为1.0。",
            "- 正式Emotion为0/0/50占位输入。",
            "- 历史报告仅Top5000保留完整factor_detail。",
            "",
            "## Algorithm Design Changes",
            "",
            "- S3差异化Emotion是新算法设计，不是简单Bug修复。",
            "- S4仅在五大因子组完成固定区间/Band计算后做全A百分位，是新归一化设计。",
            "- Global Emotion是否改为Gate需要前向验证和决策。",
            "",
            "## 关键结果",
            "",
            f"- S0总分区间：`{versions['S0_LEGACY']['score_min']:.4f}–{versions['S0_LEGACY']['score_max']:.4f}`",
            f"- S1 Top100重合率：`{comparisons['S1_AMOUNT_UNIT_FIX']['top100_overlap']:.2%}`",
            f"- S2 Top100重合率：`{comparisons['S2_REAL_VOLUME_RATIO']['top100_overlap']:.2%}`",
            f"- S3 Global Top100重合率：`{comparisons['S3_GLOBAL_EMOTION_REAL']['top100_overlap']:.2%}`",
            f"- S3 Differentiated Top100重合率：`{comparisons['S3_DIFFERENTIATED_EMOTION']['top100_overlap']:.2%}`",
            f"- S4 Top100重合率：`{comparisons['S4_PERCENTILE_NORMALIZATION']['top100_overlap']:.2%}`",
            f"- Moneyflow缺失偏差：`{report['moneyflow_bias']['finding']}`",
            "",
            "## 安全边界",
            "",
            "- External API calls: 0",
            "- LLM calls: 0",
            "- Orders: 0",
            "- Scheduler: OFF",
            "- Production table writes: 0",
            "- Git commit: NOT EXECUTED",
            "",
        ]
    )


def build_excel(payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
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
    builder_source = ROOT / "scripts" / "build_quant_shadow_comparison_excel.mjs"
    with tempfile.TemporaryDirectory(prefix="quant-shadow-xlsx-") as temp_name:
        build_dir = Path(temp_name)
        builder = build_dir / builder_source.name
        shutil.copy2(builder_source, builder)
        payload_path = build_dir / "payload.json"
        write_json(payload_path, payload)
        link = build_dir / "node_modules"
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(dependency_root)],
            capture_output=True,
            text=True,
            check=False,
        )
        if junction.returncode != 0:
            raise RuntimeError("ARTIFACT_TOOL_JUNCTION_FAILED")
        completed = subprocess.run(
            ["node", str(builder), str(payload_path), str(output_path)],
            cwd=build_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=420,
        )
        validation_path = output_path.with_suffix(".validation.json")
        teardown_fault = completed.returncode in {3221226505, -1073740791}
        complete = output_path.exists() and validation_path.exists()
        if (completed.returncode != 0 and not (teardown_fault and complete)) or not complete:
            raise RuntimeError(
                f"SHADOW_WORKBOOK_EXPORT_FAILED:{completed.returncode}:"
                f"{completed.stderr[-1000:]}"
            )
        sidecar = Path(f"{output_path}.inspect.ndjson")
        sidecar.unlink(missing_ok=True)
        validation = read_json(validation_path)
        validation_path.unlink(missing_ok=True)
        validation["artifact_tool_process_exit_code"] = completed.returncode
        validation["native_teardown_warning"] = teardown_fault
        return validation


def workbook_payload(
    report: dict[str, Any],
    versions: dict[str, list[dict[str, Any]]],
    amount_modules: list[dict[str, Any]],
    universe_audit: list[dict[str, Any]],
    fallback_rows: list[dict[str, Any]],
    bias_rows: list[dict[str, Any]],
    emotion_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    top100_changes: list[dict[str, Any]],
    variance_rows: list[dict[str, Any]],
    sector_rows: list[dict[str, Any]],
    collision_rows: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
    workbook_hash: dict[str, Any],
) -> dict[str, Any]:
    overview = [
        {"项目": "Phase", "值": report["phase"]},
        {"项目": "Base run", "值": BASE_RUN_ID},
        {"项目": "Trade date", "值": TRADE_DATE},
        {"项目": "Input hash", "值": BASE_INPUT_HASH},
        {"项目": "Fixed universe", "值": report["fixed_universe"]},
        {
            "项目": "Amount bug",
            "值": "daily.amount THOUSAND_CNY was used as CNY",
        },
        {"项目": "Moneyflow bias", "值": report["moneyflow_bias"]["finding"]},
        {
            "项目": "Largest marginal change",
            "值": report["largest_marginal_change"],
        },
        {"项目": "Final status", "值": report["final_status"]},
    ]
    version_sheet_names = {
        "S0_LEGACY": "07_S0正式基线",
        "S1_AMOUNT_UNIT_FIX": "08_S1单位修复",
        "S2_REAL_VOLUME_RATIO": "09_S2真实量比",
        "S3_GLOBAL_EMOTION_REAL": "10_S3全局情绪",
        "S3_DIFFERENTIATED_EMOTION": "11_S3差异化情绪",
        "S4_PERCENTILE_NORMALIZATION": "12_S4百分位",
    }
    sheets: dict[str, list[dict[str, Any]]] = {
        "01_研究总览": overview,
        "02_成交额影响面": amount_modules,
        "03_Universe排除": universe_audit,
        "04_缺失与回退": fallback_rows,
        "05_Moneyflow偏差": bias_rows,
        "06_Emotion设计": emotion_rows,
    }
    for version, sheet_name in version_sheet_names.items():
        sheets[sheet_name] = [
            {
                "rank": row["rank"],
                "stock_code": normalize_code(row["stock_code"]),
                "stock_name": row.get("stock_name"),
                "industry": row.get("level_one_sector") or row.get("industry"),
                "total_score": row["total_score"],
                "technical_score": row["technical_score"],
                "capital_score": row["capital_score"],
                "emotion_score": row["emotion_score"],
                "momentum_score": row["momentum_score"],
                "risk_score": row["risk_score"],
            }
            for row in versions[version]
        ]
    sheets.update(
        {
            "13_版本排名对比": comparison_rows,
            "14_Top100进出": top100_changes,
            "15_因子方差贡献": variance_rows,
            "16_行业集中变化": sector_rows,
            "17_Capital同分追踪": collision_rows,
            "18_全A明细覆盖": detail_rows,
            "19_工作簿Hash": [
                {"检查项": key, "结果": value}
                for key, value in workbook_hash.items()
            ],
            "20_问题与建议": report["issue_rows"],
            "21_运行审计": [
                {"检查项": key, "结果": value}
                for key, value in report["runtime_audit"].items()
            ],
        }
    )
    return {"sheets": sheets}


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.no_external_api or not args.no_llm or not args.shadow_only:
        raise RuntimeError("SHADOW_SAFETY_FLAGS_REQUIRED")
    if not BASE_REPORT.exists() or not BASE_DATABASE.exists() or not BASE_WORKBOOK.exists():
        raise RuntimeError("BASE_INPUT_MISSING")
    output_dir = Path(args.output_dir).resolve() if args.output_dir else DEFAULT_OUTPUT
    output_dir.mkdir(parents=True, exist_ok=True)
    production_before = {
        "size": BASE_DATABASE.stat().st_size,
        "mtime_ns": BASE_DATABASE.stat().st_mtime_ns,
        "workbook_sha": file_sha256(BASE_WORKBOOK),
        "source_hashes": source_hashes(),
    }
    config = read_json(SHADOW_CONFIG) if SHADOW_CONFIG.suffix == ".json" else yaml.safe_load(
        SHADOW_CONFIG.read_text(encoding="utf-8")
    )
    config = config["shadow_quant_factor"]
    if config["input_hash"] != BASE_INPUT_HASH:
        raise RuntimeError("SHADOW_INPUT_HASH_MISMATCH")
    report_payload = read_json(BASE_REPORT)
    base_rows = [dict(row) for row in report_payload["all_scored_stocks"]]
    if len(base_rows) != 5310:
        raise RuntimeError("BASE_UNIVERSE_COUNT_MISMATCH")
    base_codes = {normalize_code(row["stock_code"]) for row in base_rows}
    histories = daily_history()
    stock_master = read_json(STOCK_BASIC_CACHE)
    stock_by_code = by_code(stock_master)
    daily_current = cache_rows("daily")
    daily_current_by_code = by_code(daily_current)
    daily_prior_by_code = by_code(cache_rows("daily", "20260721"))
    basic_by_code = by_code(cache_rows("daily_basic"))
    flow_by_code = by_code(cache_rows("moneyflow"))
    limit_by_code = by_code(cache_rows("stk_limit"))
    for code, item in stock_by_code.items():
        item["circ_mv"] = float((basic_by_code.get(code) or {}).get("circ_mv") or 0)
    universe_audit, universe_summary = build_universe_audit(
        stock_master,
        histories,
        base_codes,
        int(config["universe"]["min_kline_bars"]),
        float(config["universe"]["configured_min_daily_amount_cny"]),
    )
    global_score, global_rows, global_summary = global_emotion(
        daily_current,
        daily_prior_by_code,
        limit_by_code,
        config["global_emotion"],
    )
    codes = [normalize_code(row["stock_code"]) for row in base_rows]
    diff_scores, diff_detail, diff_rows, diff_summary = differentiated_emotion(
        codes,
        stock_by_code,
        daily_current_by_code,
        daily_prior_by_code,
        limit_by_code,
        config["differentiated_emotion"],
    )
    versions, version_detail = build_versions(
        base_rows,
        histories,
        basic_by_code,
        flow_by_code,
        diff_scores,
        global_score,
    )
    summaries = {
        version: version_summary(version, ranked, stock_by_code)
        for version, ranked in versions.items()
    }
    comparisons = {
        version: compare_versions(
            versions["S0_LEGACY"],
            ranked,
            summaries["S0_LEGACY"],
            summaries[version],
        )
        for version, ranked in versions.items()
    }
    fallback_rows, fallback_summary = fallback_audit(
        codes, histories, basic_by_code, flow_by_code
    )
    bias_rows, bias_summary = moneyflow_bias(
        versions["S0_LEGACY"],
        flow_by_code,
        stock_by_code,
        histories,
        basic_by_code,
    )
    amount_modules = amount_impact_modules(
        versions["S0_LEGACY"],
        versions["S1_AMOUNT_UNIT_FIX"],
        universe_summary,
    )
    amount_stock_rows = []
    s0_by = {
        normalize_code(row["stock_code"]): row for row in versions["S0_LEGACY"]
    }
    s1_by = {
        normalize_code(row["stock_code"]): row
        for row in versions["S1_AMOUNT_UNIT_FIX"]
    }
    for code in codes:
        data = version_detail["S1_AMOUNT_UNIT_FIX"][code]
        amount_stock_rows.append(
            {
                "stock_code": code,
                "raw_amount_thousand_cny": data["raw_amount"],
                "normalized_amount_cny": data["normalized_amount"],
                "legacy_amount_score": data["legacy_amount_score"],
                "corrected_amount_score": data["corrected_amount_score"],
                "legacy_liquidity_score": data["legacy_liquidity_score"],
                "corrected_liquidity_score": data["corrected_liquidity_score"],
                "s0_capital_score": s0_by[code]["capital_score"],
                "s1_capital_score": s1_by[code]["capital_score"],
                "s0_risk_score": s0_by[code]["risk_score"],
                "s1_risk_score": s1_by[code]["risk_score"],
                "s0_rank": s0_by[code]["rank"],
                "s1_rank": s1_by[code]["rank"],
                "rank_delta": abs(int(s0_by[code]["rank"]) - int(s1_by[code]["rank"])),
            }
        )
    collision_rows = capital_collision_lineage(
        versions["S0_LEGACY"], histories, basic_by_code, flow_by_code
    )
    connection = open_read_only(BASE_DATABASE)
    try:
        detail_rows = factor_detail_persistence_audit(connection, report_payload)
        recorded_hash = None
        if BASE_AUDIT.exists():
            recorded_hash = read_json(BASE_AUDIT)["workbook"].get(
                "official_recorded_sha256"
            )
        workbook_hash = workbook_hash_audit(BASE_WORKBOOK, recorded_hash)
        prior_audit = read_json(BASE_AUDIT) if BASE_AUDIT.exists() else {}
    finally:
        connection.close()
    config_hash = canonical_hash(
        {
            "config": config,
            "script_hash": production_before["source_hashes"]["shadow_script"],
        }
    )
    logic_hash = production_before["source_hashes"]["shadow_script"]
    comparison_rows = []
    top100_changes = []
    variance_rows = []
    sector_rows = []
    for version in VERSIONS:
        comparison = comparisons[version]
        comparison_rows.append(
            {
                **{key: value for key, value in comparison.items() if not isinstance(value, list)},
                "fixed_universe_count": len(versions[version]),
                "natural_universe_count": len(versions[version]),
                "input_hash": BASE_INPUT_HASH,
            }
        )
        for direction, members in (
            ("ENTERED_TOP100", comparison["entered_top100"]),
            ("EXITED_TOP100", comparison["exited_top100"]),
        ):
            for code in members:
                top100_changes.append(
                    {
                        "version": version,
                        "direction": direction,
                        "stock_code": code,
                        "stock_name": (stock_by_code.get(code) or {}).get("name"),
                        "industry": (stock_by_code.get(code) or {}).get("industry"),
                        "s0_rank": s0_by[code]["rank"],
                        "version_rank": next(
                            row["rank"]
                            for row in versions[version]
                            if normalize_code(row["stock_code"]) == code
                        ),
                    }
                )
        for factor, stats in summaries[version]["factor_stats"].items():
            variance_rows.append(
                {
                    "version": version,
                    "factor": factor,
                    "mean": stats["mean"],
                    "variance": stats["variance"],
                    "unique_score_count": stats["unique"],
                    "equal_50_count": stats["equal_50"],
                }
            )
        sector_rows.append(
            {
                "version": version,
                "top100_sector_hhi": summaries[version]["sector_hhi_top100"],
                "top100_sector_count": summaries[version]["top100_sector_count"],
                "delta_vs_s0": comparisons[version]["sector_concentration_delta"],
            }
        )
    marginal = {}
    previous = "S0_LEGACY"
    for version in VERSIONS[1:]:
        prior_top = set(summaries[previous]["top100"])
        current_top = set(summaries[version]["top100"])
        marginal[version] = len(prior_top ^ current_top)
        previous = version
    largest_marginal = max(marginal, key=marginal.get)
    research_db = Path(args.research_db).resolve() if args.research_db else DEFAULT_RESEARCH_DB
    shadow_connection = initialize_shadow_db(research_db)
    try:
        shadow_metadata = persist_shadow(
            shadow_connection,
            versions,
            summaries,
            comparisons,
            universe_audit,
            version_detail,
            diff_detail,
            global_score,
            config,
            config_hash,
            logic_hash,
        )
        detail_persisted_counts = {
            version: shadow_connection.execute(
                "SELECT COUNT(DISTINCT stock_code) FROM quant_shadow_factor_detail "
                "WHERE shadow_run_id=?",
                (shadow_metadata[version]["shadow_run_id"],),
            ).fetchone()[0]
            for version in VERSIONS
        }
    finally:
        shadow_connection.close()
    issue_rows = [
        {
            "category": "CONFIRMED_BUG_FIX",
            "issue": "daily.amount unit",
            "finding": "THOUSAND_CNY must be converted x1000 before CNY thresholds",
        },
        {
            "category": "CONFIRMED_BUG_FIX",
            "issue": "volume_ratio",
            "finding": "cached real field is ignored and replaced with 1.0",
        },
        {
            "category": "CONFIRMED_BUG_FIX",
            "issue": "emotion placeholder",
            "finding": "formal provider emits 0/0/50",
        },
        {
            "category": "ALGORITHM_DESIGN_CHANGE",
            "issue": "differentiated emotion",
            "finding": "new sector/stock formula requires forward validation",
        },
        {
            "category": "ALGORITHM_DESIGN_CHANGE",
            "issue": "factor-group percentile",
            "finding": "S4 changes normalization after preserving subfactor Band/Clip semantics",
        },
        {
            "category": "DATA_COVERAGE_PROBLEM",
            "issue": "moneyflow coverage",
            "finding": bias_summary["finding"],
        },
        {
            "category": "HISTORICAL_AUDIT_PROBLEM",
            "issue": "workbook immutable artifact",
            "finding": workbook_hash["diagnosis"],
        },
        {
            "category": "HISTORICAL_AUDIT_PROBLEM",
            "issue": "factor detail",
            "finding": "bottom 310 report rows lack historical subfactor detail",
        },
    ]
    report = {
        "phase": "Quant Factor Remediation Preparation + Immutable Shadow Counterfactual V1",
        "base_run": BASE_RUN_ID,
        "trade_date": TRADE_DATE,
        "base_factor_version": BASE_FACTOR_VERSION,
        "input_hash": BASE_INPUT_HASH,
        "config_hash": config_hash,
        "fixed_universe": len(base_codes),
        "universe_summary": universe_summary,
        "amount_unit_affected_modules": amount_modules,
        "amount_unit_affected_stocks": sum(
            row["affected_stock_count"]
            for row in amount_modules
            if row["module"] == "Quant Capital"
        ),
        "hard_gate_flips": universe_summary["configured_gate_flip_count"],
        "moneyflow_bias": bias_summary,
        "fallback_summary": fallback_summary,
        "global_emotion": global_summary,
        "differentiated_emotion": diff_summary,
        "version_summaries": summaries,
        "comparisons": comparisons,
        "marginal_top100_flip_count": marginal,
        "largest_marginal_change": largest_marginal,
        "capital_collision_lineage": collision_rows,
        "factor_detail_persistence_audit": detail_rows,
        "shadow_factor_detail_coverage": detail_persisted_counts,
        "workbook_hash_audit": workbook_hash,
        "shadow_runs": shadow_metadata,
        "research_database": str(research_db),
        "issue_rows": issue_rows,
        "confirmed_bugs": [
            "daily.amount unit conversion",
            "real volume_ratio integration",
            "formal emotion placeholder",
            "factor detail persistence scope",
        ],
        "algorithm_design_changes": [
            "differentiated emotion formula",
            "factor-group percentile normalization",
            "possible Global Emotion gate/deployment use",
        ],
        "data_coverage_issues": [
            f"moneyflow missing {bias_summary['missing']} stocks",
            f"industry mapping missing {diff_summary['unknown_industry_count']} stocks",
        ],
        "known_limitations": [
            "Same-day counterfactual only; no D1/D3/D5 return is used.",
            "Production amount gate is not wired, so formal Universe does not flip from unit correction alone.",
            "Original workbook bytes for recorded SHA are unavailable; initial content/style deltas cannot be reconstructed.",
            "S0 bottom310 historical subfactor rows were not persisted; shadow keeps historical group scores and reconstructs only auditable changed-factor inputs.",
        ],
        "recommended_next_step": "Freeze all six immutable candidates and perform blind D1/D3/D5 forward outcome backfill; do not promote S4 automatically.",
        "runtime_audit": {
            "external_api_calls": 0,
            "llm_calls": 0,
            "orders": 0,
            "scheduler": "OFF",
            "real_orders": 0,
            "virtual_orders": 0,
            "production_table_writes": 0,
            "production_config_changes": 0,
            "historical_results_overwritten": 0,
            "shadow_database": str(research_db),
            "git_commit": "NOT_EXECUTED",
            "quant_hash_before": production_before["source_hashes"]["quant"],
            "flash_hash": (prior_audit.get("runtime_audit") or {}).get("flash_hash"),
            "pro_hash": (prior_audit.get("runtime_audit") or {}).get("pro_hash"),
        },
        "final_status": "MULTIPLE_REMEDIATIONS_REQUIRED",
        "suggested_commit": "research(quant): isolate factor bugs with immutable shadow counterfactuals",
    }
    output_paths = {
        "json": output_dir / "quant_remediation_audit.json",
        "markdown": output_dir / "quant_remediation_audit.md",
        "amount": output_dir / "amount_unit_impact.csv",
        "universe": output_dir / "universe_exclusion_audit.csv",
        "fallback": output_dir / "missing_fallback_audit.csv",
        "bias": output_dir / "moneyflow_missing_bias.csv",
        "emotion": output_dir / "emotion_design_audit.csv",
        "comparison": output_dir / "shadow_version_comparison.csv",
        "top100": output_dir / "top100_membership_changes.csv",
        "detail": output_dir / "factor_detail_persistence_audit.csv",
        "workbook_hash": output_dir / "workbook_hash_audit.json",
        "excel": output_dir / "quant_factor_shadow_comparison.xlsx",
    }
    write_csv(output_paths["amount"], amount_stock_rows)
    write_csv(output_paths["universe"], universe_audit)
    write_csv(output_paths["fallback"], fallback_rows)
    write_csv(output_paths["bias"], bias_rows)
    emotion_rows = global_rows + diff_rows
    write_csv(output_paths["emotion"], emotion_rows)
    write_csv(output_paths["comparison"], comparison_rows)
    write_csv(output_paths["top100"], top100_changes)
    write_csv(output_paths["detail"], detail_rows)
    write_json(output_paths["workbook_hash"], workbook_hash)
    write_json(output_paths["json"], report)
    output_paths["markdown"].write_text(markdown_report(report), encoding="utf-8")
    excel_payload = workbook_payload(
        report,
        versions,
        amount_modules,
        universe_audit,
        fallback_rows,
        bias_rows,
        emotion_rows,
        comparison_rows,
        top100_changes,
        variance_rows,
        sector_rows,
        collision_rows,
        detail_rows,
        workbook_hash,
    )
    excel_validation = build_excel(excel_payload, output_paths["excel"])
    report["excel"] = {
        "path": str(output_paths["excel"]),
        "sha256": file_sha256(output_paths["excel"]),
        "validation": excel_validation,
    }
    report["reports"] = [str(path) for key, path in output_paths.items() if key != "excel"]
    production_after = {
        "size": BASE_DATABASE.stat().st_size,
        "mtime_ns": BASE_DATABASE.stat().st_mtime_ns,
        "workbook_sha": file_sha256(BASE_WORKBOOK),
        "source_hashes": source_hashes(),
    }
    report["runtime_audit"]["quant_hash_after"] = production_after["source_hashes"][
        "quant"
    ]
    report["runtime_audit"]["quant_hash_unchanged"] = (
        production_before["source_hashes"]["quant"]
        == production_after["source_hashes"]["quant"]
    )
    report["runtime_audit"]["production_database_unchanged"] = (
        production_before["size"] == production_after["size"]
        and production_before["mtime_ns"] == production_after["mtime_ns"]
    )
    report["runtime_audit"]["historical_workbook_unchanged"] = (
        production_before["workbook_sha"] == production_after["workbook_sha"]
    )
    if not all(
        (
            report["runtime_audit"]["quant_hash_unchanged"],
            report["runtime_audit"]["production_database_unchanged"],
            report["runtime_audit"]["historical_workbook_unchanged"],
        )
    ):
        raise RuntimeError("PRODUCTION_IMMUTABILITY_VIOLATION")
    write_json(output_paths["json"], report)
    output_paths["markdown"].write_text(markdown_report(report), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Immutable local-cache-only Quant shadow counterfactual research"
    )
    parser.add_argument("--base-run-id", default=BASE_RUN_ID)
    parser.add_argument("--trade-date", default=TRADE_DATE)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--research-db", default="")
    parser.add_argument("--no-external-api", action="store_true", default=True)
    parser.add_argument("--no-llm", action="store_true", default=True)
    parser.add_argument("--shadow-only", action="store_true", default=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.base_run_id != BASE_RUN_ID or args.trade_date != TRADE_DATE:
        print(
            json.dumps(
                {"final_status": "SHADOW_RESEARCH_BLOCKED", "error": "BASE_RUN_MISMATCH"}
            )
        )
        return 2
    try:
        report = run(args)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "final_status": "SHADOW_RESEARCH_BLOCKED",
                    "error": f"{exc.__class__.__name__}:{exc}",
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "phase": report["phase"],
                "base_run": report["base_run"],
                "trade_date": report["trade_date"],
                "final_status": report["final_status"],
                "excel": report["excel"],
                "reports": report["reports"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
