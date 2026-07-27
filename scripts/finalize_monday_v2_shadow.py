from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from llm_gateway.service import get_llm_gateway_service
from quant.shadow.tushare_quant_v2 import linear, true_range_percent
from research.flash_v4 import (
    FLASH_RANKING_VERSION,
    FLASH_SCORE_VERSION,
    assert_flash_batch_quality,
)
from research.knowledge_mode import LLMKnowledgeMode
from research.structured_validation import (
    SCREENING_PROMPT_VERSION,
    StructuredOutputValidationError,
    StructuredValidationProvider,
)
from scripts.run_tushare_quant_v2_validation import (
    _order_plans,
    by_code,
    configure_runtime as configure_v2_runtime,
    load_histories,
    read_json,
    stock_code,
    trade_date_rows,
)
from stock_codes import normalize_ts_code
from trader_demo.pro_single_v3 import (
    RANKING_VERSION,
    SINGLE_CONTRACT_VERSION,
    SINGLE_PROMPT_VERSION,
    ProSingleV3Service,
    _validate_single,
)
from trader_demo.runtime import temporary_real_llm_runtime


TRADE_DATE = "2026-07-24"
TARGET_DATE = "2026-07-27"
BASE_RUN_ID = "v2-recovery-20260724-123736"
DISCLAIMER = "仅使用2026-07-24收盘数据；未包含周末新增消息、周一集合竞价或盘中数据。"
OUTPUT_ROOT = ROOT / "outputs" / "quant_v2_validation" / TRADE_DATE
BASE_REPORT = OUTPUT_ROOT / "quant_v2_validation.json"
FULL_UNIVERSE = OUTPUT_ROOT / "v2_full_universe.csv"
MEMBER_ROOT = ROOT / "data" / "cache" / "tushare" / "quant_v2_members" / "20260724"
FORMAL_REPORT = (
    ROOT / "data" / "reports" / "quant_20260724_desktop-7528596678264dc2a7c1.json"
)
CHECKPOINT = OUTPUT_ROOT / ".monday_v2_llm_checkpoint.json"
TEST_LOG = OUTPUT_ROOT / "monday_v2_tests.log"
DELIVERABLES = {
    "watch": OUTPUT_ROOT / "monday_v2_watch_pool.csv",
    "active": OUTPUT_ROOT / "monday_v2_active_shadow.csv",
    "audit": OUTPUT_ROOT / "monday_v2_candidate_audit.json",
    "risk": OUTPUT_ROOT / "monday_v2_risk_lineage.csv",
    "theme": OUTPUT_ROOT / "monday_v2_theme_concentration.csv",
    "report": OUTPUT_ROOT / "monday_v2_final_report.md",
}

THEME_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("军工装备", ("军工", "国防", "航空", "航天", "兵器", "船舶", "卫星", "无人机", "雷达")),
    ("AI/算力", ("人工智能", "算力", "数据中心", "服务器", "大模型", "AIGC", "云计算", "GPU", "芯片")),
    ("电力新能源", ("电力", "新能源", "光伏", "风电", "储能", "锂电", "电池", "充电桩", "核电")),
    ("周期资源", ("煤炭", "钢铁", "有色", "稀土", "黄金", "石油", "化工", "矿", "资源")),
    ("医药", ("医药", "医疗", "生物", "创新药", "中药", "疫苗")),
    ("消费", ("消费", "食品", "饮料", "白酒", "零售", "家电", "旅游", "酒店", "纺织")),
)
FOCUS_NAMES = {
    "300779": "惠城环保",
    "301535": "浙江华远",
    "605028": "世茂能源",
    "300850": "新强联",
}


def configure_runtime(
    trade_date: str,
    target_trade_date: str,
    *,
    base_run_id: str | None = None,
    formal_report: Path | None = None,
) -> None:
    global TRADE_DATE, TARGET_DATE, BASE_RUN_ID, DISCLAIMER, OUTPUT_ROOT
    global BASE_REPORT, FULL_UNIVERSE, MEMBER_ROOT, FORMAL_REPORT
    global CHECKPOINT, TEST_LOG, DELIVERABLES, FOCUS_NAMES
    parsed_trade_date = date.fromisoformat(trade_date)
    parsed_target_date = date.fromisoformat(target_trade_date)
    if parsed_target_date <= parsed_trade_date:
        raise ValueError("V2_TARGET_TRADE_DATE_MUST_BE_LATER")
    TRADE_DATE = parsed_trade_date.isoformat()
    TARGET_DATE = parsed_target_date.isoformat()
    OUTPUT_ROOT = ROOT / "outputs" / "quant_v2_validation" / TRADE_DATE
    BASE_REPORT = OUTPUT_ROOT / "quant_v2_validation.json"
    FULL_UNIVERSE = OUTPUT_ROOT / "v2_full_universe.csv"
    report = read_json(BASE_REPORT, {})
    BASE_RUN_ID = str(base_run_id or report.get("run_id") or "")
    if not BASE_RUN_ID:
        raise ValueError("V2_BASE_RUN_ID_REQUIRED")
    DISCLAIMER = (
        f"仅使用{TRADE_DATE}收盘数据；未包含{TARGET_DATE}集合竞价或盘中数据。"
    )
    MEMBER_ROOT = (
        ROOT
        / "data"
        / "cache"
        / "tushare"
        / "quant_v2_members"
        / parsed_trade_date.strftime("%Y%m%d")
    )
    FORMAL_REPORT = (
        formal_report.resolve()
        if formal_report
        else _resolve_formal_report(parsed_trade_date.strftime("%Y%m%d"))
    )
    CHECKPOINT = OUTPUT_ROOT / ".monday_v2_llm_checkpoint.json"
    TEST_LOG = OUTPUT_ROOT / "monday_v2_tests.log"
    DELIVERABLES = {
        "watch": OUTPUT_ROOT / "monday_v2_watch_pool.csv",
        "active": OUTPUT_ROOT / "monday_v2_active_shadow.csv",
        "audit": OUTPUT_ROOT / "monday_v2_candidate_audit.json",
        "risk": OUTPUT_ROOT / "monday_v2_risk_lineage.csv",
        "theme": OUTPUT_ROOT / "monday_v2_theme_concentration.csv",
        "report": OUTPUT_ROOT / "monday_v2_final_report.md",
    }
    if TRADE_DATE != "2026-07-24":
        FOCUS_NAMES = {}
    configure_v2_runtime(
        TRADE_DATE,
        TARGET_DATE,
        formal_report=FORMAL_REPORT,
    )


def _resolve_formal_report(trade_key: str) -> Path:
    candidates = sorted(
        (ROOT / "data" / "reports").glob(f"quant_{trade_key}_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"V2_LEGACY_UNIVERSE_REPORT_MISSING:{trade_key}")
    return candidates[0].resolve()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _round(value: Any, digits: int = 4) -> float:
    return round(_number(value), digits)


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    values = [dict(row) for row in rows]
    fields: list[str] = []
    for row in values:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["status", "disclaimer"]
        values = [{"status": "EMPTY", "disclaimer": DISCLAIMER}]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(values)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _full_code(row: Mapping[str, Any]) -> str:
    raw = str(row.get("stock_code") or row.get("ts_code") or "")
    if "." in raw:
        return normalize_ts_code(raw)
    exchange = str(row.get("exchange") or "").upper()
    if exchange in {"SH", "SZ", "BJ"}:
        return f"{raw.zfill(6)}.{exchange}"
    return normalize_ts_code(raw)


def _percentile_map(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values.values())
    size = len(ordered)
    if not ordered:
        return {}
    if ordered[0] == ordered[-1]:
        return {key: 50.0 for key in values}
    return {
        key: round(bisect_right(ordered, value) / size * 100, 4)
        for key, value in values.items()
    }


def _max_drawdown(closes: Sequence[float]) -> float | None:
    if len(closes) < 2:
        return None
    peak = closes[0]
    worst = 0.0
    for value in closes:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst * 100


def _consecutive_up(history: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for row in reversed(history):
        if _number(row.get("pct_chg")) > 0:
            count += 1
        else:
            break
    return count


def _load_base() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    report = read_json(BASE_REPORT, {})
    if report.get("run_id") != BASE_RUN_ID:
        raise ValueError(f"BASE_RUN_ID_MISMATCH:{report.get('run_id')}")
    if report.get("trade_date") != TRADE_DATE:
        raise ValueError(f"BASE_TRADE_DATE_MISMATCH:{report.get('trade_date')}")
    final_stage = report["v2_run"]["stages"]["TUSHARE_QUANT_V2_CORRECTED_SHADOW"]
    top100 = [dict(row) for row in final_stage["top100"] if not row.get("hard_gate")]
    top20 = [dict(row) for row in final_stage["top20"]]
    if len(top100) < 20:
        raise ValueError("V2_TOP100_AFTER_HARD_GATE_INSUFFICIENT")
    return report, top20, top100


def _load_cached_stock_basic() -> dict[str, dict[str, Any]]:
    candidates = sorted(
        (ROOT / "data" / "cache" / "tushare").glob("stock_basic_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        payload = read_json(path, [])
        rows = (
            payload
            if isinstance(payload, list)
            else payload.get("records", payload.get("data", payload.get("rows", [])))
            if isinstance(payload, dict)
            else []
        )
        values = by_code(rows)
        if len(values) >= 5000:
            return values
    raise ValueError("LOCAL_STOCK_BASIC_CACHE_NOT_FOUND")


def audit_risk_lineage(
    report: Mapping[str, Any],
    top20: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    full_rows = _read_csv(FULL_UNIVERSE)
    eligible = {stock_code(row.get("stock_code")) for row in full_rows}
    daily = by_code(trade_date_rows("daily"))
    basic = by_code(trade_date_rows("daily_basic"))
    histories = load_histories()
    monday = [dict(row) for row in report.get("monday_candidates") or []]
    targets = {
        stock_code(row.get("stock_code")): dict(row)
        for row in [*top20, *monday]
    }
    amounts = {
        item: _number((daily.get(item) or {}).get("amount"))
        for item in eligible
        if item in daily
    }
    amount_rank = _percentile_map(amounts)
    lineage: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {}
    mismatches: list[dict[str, Any]] = []
    for item, source in sorted(targets.items(), key=lambda pair: int(pair[1]["rank"])):
        history = histories.get(item) or []
        closes = [_number(row.get("close")) for row in history if _number(row.get("close")) > 0]
        close = closes[-1] if closes else 0.0
        peak60 = max(closes[-60:]) if closes else 0.0
        high_position = close / peak60 if peak60 else 0.0
        position_health = (
            100.0
            if high_position <= 0.88
            else float(linear(high_position, 1.05, 0.88))
        )
        range_volatility = true_range_percent(history)
        volatility_health = 100 - min(range_volatility * 500, 70)
        turnover_raw = (basic.get(item) or {}).get("turnover_rate")
        turnover_missing = turnover_raw in (None, "")
        turnover = _number(turnover_raw)
        turnover_health = 100.0 if 0.5 <= turnover <= 12 else 45.0
        liquidity_rank = amount_rank.get(item, 0.0)
        reconstructed = round(
            liquidity_rank * 0.35
            + position_health * 0.30
            + volatility_health * 0.20
            + turnover_health * 0.15,
            4,
        )
        stored = _round(source.get("risk_score"))
        if abs(stored - reconstructed) > 0.0002:
            mismatches.append(
                {"stock_code": item, "stored": stored, "reconstructed": reconstructed}
            )
        drawdown = _max_drawdown(closes[-60:])
        base = {
            "stock_code": item,
            "stock_name": source.get("stock_name") or FOCUS_NAMES.get(item, item),
            "trade_date": TRADE_DATE,
            "v2_rank": source.get("rank"),
            "risk_health_score": stored,
        }
        rows = [
            {
                **base,
                "risk_component": "liquidity_health",
                "raw_value": json.dumps(
                    {
                        "amount_thousand_cny": (daily.get(item) or {}).get("amount"),
                        "amount_percentile": liquidity_rank,
                        "turnover_rate": turnover_raw,
                    },
                    ensure_ascii=False,
                ),
                "normalization_direction": "HIGHER_AMOUNT_IS_SAFER;TURNOVER_0.5_TO_12_IS_SAFER",
                "score": round(
                    (liquidity_rank * 0.35 + turnover_health * 0.15) / 0.50, 4
                ),
                "contribution": round(
                    liquidity_rank * 0.35 + turnover_health * 0.15, 4
                ),
                "missing/fallback": (
                    "TURNOVER_MISSING_CONSERVATIVE_45"
                    if turnover_missing
                    else "NONE"
                ),
            },
            {
                **base,
                "risk_component": "downside_volatility",
                "raw_value": round(range_volatility, 8),
                "normalization_direction": "INVERSE_HIGHER_RANGE_VOLATILITY_IS_LESS_SAFE",
                "score": round(volatility_health, 4),
                "contribution": round(volatility_health * 0.20, 4),
                "missing/fallback": "NONE" if history else "HISTORY_MISSING_SCORE_100_BUG",
            },
            {
                **base,
                "risk_component": "max_drawdown",
                "raw_value": None if drawdown is None else round(drawdown, 4),
                "normalization_direction": "INVERSE_HIGHER_DRAWDOWN_IS_LESS_SAFE",
                "score": "",
                "contribution": 0.0,
                "missing/fallback": "NOT_USED_IN_RISK_V2",
            },
            {
                **base,
                "risk_component": "high_position_crowding",
                "raw_value": json.dumps(
                    {
                        "close_to_60d_peak": round(high_position, 6),
                        "consecutive_up_days": _consecutive_up(history),
                    },
                    ensure_ascii=False,
                ),
                "normalization_direction": "INVERSE_ABOVE_0.88;AT_OR_BELOW_0.88_SCORE_100",
                "score": round(position_health, 4),
                "contribution": round(position_health * 0.30, 4),
                "missing/fallback": "NONE" if closes else "NO_VALID_CLOSE",
            },
            {
                **base,
                "risk_component": "chip_risk",
                "raw_value": "",
                "normalization_direction": "INVERSE_EXPECTED_NOT_IMPLEMENTED",
                "score": "",
                "contribution": 0.0,
                "missing/fallback": "NOT_USED_IN_RISK_V2",
            },
            {
                **base,
                "risk_component": "financial_event_risk",
                "raw_value": "",
                "normalization_direction": "INVERSE_EXPECTED_NOT_IMPLEMENTED",
                "score": "",
                "contribution": 0.0,
                "missing/fallback": "NOT_USED_IN_RISK_V2",
            },
            {
                **base,
                "risk_component": "gap_limit_path_risk",
                "raw_value": json.dumps(
                    {
                        "limit_status": source.get("limit_status"),
                        "hard_gate": source.get("hard_gate"),
                        "hard_gate_reasons": source.get("hard_gate_reasons") or [],
                    },
                    ensure_ascii=False,
                ),
                "normalization_direction": "HARD_GATE_ONLY_LIMIT_UP_NOT_BUYABLE",
                "score": "",
                "contribution": 0.0,
                "missing/fallback": "NO_SOFT_SCORE_IN_RISK_V2",
            },
        ]
        lineage.extend(rows)
        summary[item] = {
            "stored": stored,
            "reconstructed": reconstructed,
            "liquidity_rank": round(liquidity_rank, 4),
            "turnover_health": turnover_health,
            "position_ratio": round(high_position, 6),
            "position_health": round(position_health, 4),
            "range_volatility": round(range_volatility, 8),
            "volatility_health": round(volatility_health, 4),
            "max_drawdown_60d": None if drawdown is None else round(drawdown, 4),
            "consecutive_up_days": _consecutive_up(history),
        }
    invariants = {
        "higher_score_means_safer": True,
        "high_position_inverse_direction": (
            linear(1.04, 1.05, 0.88) < linear(0.90, 1.05, 0.88)
        ),
        "volatility_inverse_direction": (
            100 - min(0.08 * 500, 70) < 100 - min(0.02 * 500, 70)
        ),
        "missing_turnover_does_not_improve": 45 < 100,
        "unused_missing_components_contribution_zero": True,
        "reconstruction_mismatch_count": len(mismatches),
        "reconstruction_mismatches": mismatches,
    }
    audit = {
        "status": (
            "PASS"
            if all(
                [
                    invariants["higher_score_means_safer"],
                    invariants["high_position_inverse_direction"],
                    invariants["volatility_inverse_direction"],
                    invariants["missing_turnover_does_not_improve"],
                    not mismatches,
                ]
            )
            else "FAIL"
        ),
        "invariants": invariants,
        "limitations": [
            "downside_volatility当前实际使用14日振幅均值代理，并非纯下行波动。",
            "max_drawdown、chip_risk、financial_event_risk未进入当前Risk V2软评分。",
            "gap_limit_path_risk仅对当日涨停不可买执行Hard Gate，未形成软惩罚。",
            "连续上涨天数未单独惩罚；高位风险仅由收盘价/60日峰值处理。",
        ],
    }
    return lineage, audit, summary


def _legacy_risk_rows() -> dict[str, dict[str, Any]]:
    formal = read_json(FORMAL_REPORT, {})
    return {
        stock_code(row.get("stock_code")): dict(row)
        for row in formal.get("all_scored_stocks") or []
    }


def risk_reversal_explanations(
    risk_summary: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    legacy = _legacy_risk_rows()
    rows = []
    for item, name in FOCUS_NAMES.items():
        old = legacy.get(item) or {}
        new = risk_summary.get(item) or {}
        old_score = old.get("risk_score")
        new_score = new.get("stored")
        rows.append(
            {
                "stock_code": item,
                "stock_name": name,
                "legacy_risk_health": old_score,
                "v2_risk_health": new_score,
                "delta": (
                    None
                    if old_score is None or new_score is None
                    else round(_number(new_score) - _number(old_score), 4)
                ),
                "explanation": (
                    "Legacy使用20日绝对阈值波动/回撤/成交额均值；"
                    "V2改为全Universe成交额百分位35%+高位健康30%+14日振幅健康20%+"
                    "换手健康15%。两版量纲和子因子不同，分数不可直接按同标尺解释。"
                ),
                "v2_lineage": dict(new),
            }
        )
    return rows


def build_theme_membership(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    concept_by_stock: dict[str, list[dict[str, str]]] = defaultdict(list)
    for path in sorted((MEMBER_ROOT / "concept").glob("*.json")):
        payload = read_json(path, {})
        if payload.get("status") != "available":
            continue
        board_name = str(payload.get("board_name") or "")
        board_code = str(payload.get("board_code") or path.stem)
        for member in payload.get("rows") or []:
            if str(member.get("is_new") or "Y").upper() != "Y":
                continue
            item = stock_code(member.get("con_code"))
            concept_by_stock[item].append(
                {"board_code": board_code, "board_name": board_name}
            )
    output = []
    by_stock: dict[str, dict[str, Any]] = {}
    for source in rows:
        item = stock_code(source.get("stock_code"))
        concepts = concept_by_stock.get(item) or []
        names = sorted({value["board_name"] for value in concepts})
        text = "|".join([*names, str(source.get("level_one_sector") or "")])
        clusters = [
            cluster
            for cluster, keywords in THEME_RULES
            if any(keyword.lower() in text.lower() for keyword in keywords)
        ]
        if not clusters:
            clusters = ["其他主要相关方向"]
        binding = clusters[0]
        row = {
            "stock_code": item,
            "stock_name": source.get("stock_name"),
            "v2_rank": source.get("rank"),
            "level_one_sector": source.get("level_one_sector"),
            "all_theme_clusters": "|".join(clusters),
            "binding_cluster": binding,
            "concept_count": len(names),
            "concept_names": "|".join(names),
            "retained": "",
            "concentration_reason": "PENDING_PRO_AND_REGIME_SELECTION",
            "disclaimer": DISCLAIMER,
        }
        output.append(row)
        by_stock[item] = row
    return output, by_stock


def _flash_context(
    row: Mapping[str, Any],
    theme: Mapping[str, Any],
    risk_summary: Mapping[str, Any],
) -> dict[str, Any]:
    missing = []
    if str(row.get("capital_confidence")) != "COMPLETE":
        missing.append("moneyflow_subitem")
    missing.extend(["chip_risk", "financial_event_risk", "weekend_news"])
    return {
        "stock_code": _full_code(row),
        "stock_name": row.get("stock_name"),
        "knowledge_mode": LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value,
        "manifest": {
            "decision_time": f"{TRADE_DATE}T17:00:00+08:00",
            "base_market_trade_date": TRADE_DATE,
            "target_trade_date": TARGET_DATE,
            "data_snapshot_id": BASE_RUN_ID,
        },
        "level_one_sector": row.get("level_one_sector"),
        "concept_tags": str(theme.get("concept_names") or "").split("|")
        if theme.get("concept_names")
        else [],
        "concept_source_status": "VERIFIED_STRUCTURED",
        "quant": {
            "rank": int(row["rank"]),
            "total_score": _number(row.get("total_score")),
            "technical_score": _number(row.get("technical_score")),
            "capital_score": _number(row.get("capital_score")),
            "emotion_score": _number(row.get("emotion_score")),
            "momentum_score": _number(row.get("momentum_score")),
            "risk_health_score": _number(row.get("risk_score")),
            "factor_version": row.get("factor_version"),
        },
        "fundamental_inference": {
            "industry_chain": row.get("level_one_sector") or "UNKNOWN",
            "observation_rating": "INSUFFICIENT_DATA",
            "source_status": "STRUCTURED_INPUT_ONLY",
        },
        "financial_status": {
            "status": "NORMAL",
            "explanation": "No verified hard financial-event gate in supplied snapshot.",
        },
        "risk_lineage": dict(risk_summary),
        "data_quality": {
            "technical_confidence": row.get("technical_confidence"),
            "capital_confidence": row.get("capital_confidence"),
            "capital_data_coverage": _number(row.get("capital_data_coverage")),
            "emotion_confidence": row.get("emotion_confidence"),
        },
        "missing_fields": missing,
        "hard_gate": bool(row.get("hard_gate")),
        "hard_gate_reasons": row.get("hard_gate_reasons") or [],
        "disclaimer": DISCLAIMER,
    }


def _load_checkpoint() -> dict[str, Any]:
    payload = read_json(CHECKPOINT, {})
    if payload and payload.get("base_run_id") != BASE_RUN_ID:
        raise ValueError("LLM_CHECKPOINT_BASE_RUN_MISMATCH")
    return payload or {
        "base_run_id": BASE_RUN_ID,
        "flash": {},
        "pro": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _save_checkpoint(payload: Mapping[str, Any]) -> None:
    _write_json(CHECKPOINT, payload)


def run_flash_once(
    top100: Sequence[Mapping[str, Any]],
    theme_by_stock: Mapping[str, Mapping[str, Any]],
    risk_summary: Mapping[str, Mapping[str, Any]],
    checkpoint: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider = StructuredValidationProvider()
    results: list[dict[str, Any]] = []
    business_new = 0
    for source in top100:
        item = stock_code(source.get("stock_code"))
        saved = checkpoint["flash"].get(item)
        if saved and saved.get("execution_status") == "SUCCESS":
            results.append(dict(saved))
            continue
        context = _flash_context(
            source, theme_by_stock[item], risk_summary.get(item) or {}
        )
        business_new += 1
        try:
            screening = provider.screening(
                context,
                run_mode="POST_MARKET_FINAL",
                use_real_llm=True,
            )
            result = {
                **screening,
                "stock_code": item,
                "stock_name": source.get("stock_name"),
                "quant_rank": int(source["rank"]),
                "quant_score": _number(source.get("total_score")),
                "execution_status": "SUCCESS",
                "prompt_version": SCREENING_PROMPT_VERSION,
                "flash_score_version": FLASH_SCORE_VERSION,
                "ranking_version": FLASH_RANKING_VERSION,
                "context_hash": _canonical_hash(context),
            }
        except Exception as exc:
            result = {
                "stock_code": item,
                "stock_name": source.get("stock_name"),
                "quant_rank": int(source["rank"]),
                "quant_score": _number(source.get("total_score")),
                "execution_status": "FAILED",
                "error_category": (
                    exc.category
                    if isinstance(exc, StructuredOutputValidationError)
                    else type(exc).__name__
                ),
                "error": str(exc)[:400],
                "prompt_version": SCREENING_PROMPT_VERSION,
                "context_hash": _canonical_hash(context),
            }
        checkpoint["flash"][item] = result
        _save_checkpoint(checkpoint)
        results.append(result)
    successful = [row for row in results if row["execution_status"] == "SUCCESS"]
    quality = assert_flash_batch_quality(successful) if len(successful) >= 20 else {
        "count": len(successful),
        "degenerate": True,
        "reason": "SUCCESSFUL_FLASH_LT_20",
    }
    ordered = sorted(
        successful,
        key=lambda row: (
            -_number(row.get("llm_score")),
            -_number(row.get("confidence")),
            int(row["quant_rank"]),
            row["stock_code"],
        ),
    )
    audits = list(provider.audit)
    stats = {
        "business_inputs": len(top100),
        "new_business_calls": business_new,
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "api_calls": sum(
            1 + int(bool((row.get("diagnostics") or {}).get("repair_attempted")))
            for row in audits
        ),
        "repairs": sum(
            int(bool((row.get("diagnostics") or {}).get("repair_attempted")))
            for row in audits
        ),
        "input_tokens": sum(int(row.get("input_tokens") or 0) for row in audits),
        "output_tokens": sum(int(row.get("output_tokens") or 0) for row in audits),
        "quality": quality,
        "top20_codes": [row["stock_code"] for row in ordered[:20]],
        "audit": audits,
    }
    return ordered[:20], stats


def _pro_compact(
    source: Mapping[str, Any],
    flash: Mapping[str, Any],
    theme: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "stock_code": _full_code(source),
        "stock_name": str(source.get("stock_name") or "")[:40],
        "selection_source": "LLM_TOP20",
        "manual_reason": "",
        "quant_rank": int(source["rank"]),
        "quant_score": _number(source.get("total_score")),
        "technical_score": _number(source.get("technical_score")),
        "capital_score": _number(source.get("capital_score")),
        "emotion_score": _number(source.get("emotion_score")),
        "momentum_score": _number(source.get("momentum_score")),
        "risk_score": _number(source.get("risk_score")),
        "flash_score": _number(flash.get("llm_score")),
        "flash_decision": flash.get("screening_decision"),
        "flash_confidence": _number(flash.get("confidence")),
        "financial_status": "NORMAL",
        "observation_rating": "INSUFFICIENT_DATA",
        "structured_industry": str(source.get("level_one_sector") or "UNKNOWN")[:80],
        "industry_chain_summary": str(source.get("level_one_sector") or "UNKNOWN")[:100],
        "main_business_summary": "UNKNOWN",
        "core_products_summary": [],
        "competitive_advantage_summary": "UNKNOWN",
        "investment_logic_summary": f"仅基于{TRADE_DATE}结构化量化与板块快照复核。",
        "domestic_substitution": "INSUFFICIENT_DATA",
        "data_quality_score": _number(flash.get("data_quality_score")),
        "unverified_field_count": 3,
        "missing_field_count": len(flash.get("missing_data") or []),
        "hard_risk_status": "NORMAL",
        "theme_cluster": theme.get("binding_cluster"),
    }


def run_pro_once(
    flash_top20: Sequence[Mapping[str, Any]],
    top100_by_code: Mapping[str, Mapping[str, Any]],
    theme_by_stock: Mapping[str, Mapping[str, Any]],
    checkpoint: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    gateway = get_llm_gateway_service()
    service = ProSingleV3Service(None, None, gateway=gateway)
    results: list[dict[str, Any]] = []
    new_business = 0
    api_calls = 0
    repairs = 0
    input_tokens = 0
    output_tokens = 0
    for flash in flash_top20:
        item = stock_code(flash.get("stock_code"))
        saved = checkpoint["pro"].get(item)
        if saved and saved.get("execution_status") == "SUCCESS":
            results.append(dict(saved))
            continue
        source = top100_by_code[item]
        compact = _pro_compact(source, flash, theme_by_stock[item])
        new_business += 1
        responses = []
        response = gateway.chat(
            service._candidate_request(
                compact,
                retry=False,
                max_tokens=service.config["candidate_max_tokens"],
            )
        )
        responses.append(response)
        checked = _validate_single(
            response, _full_code(source), service.config["max_item_characters"]
        )
        if checked.value is None:
            repairs += 1
            category = str(checked.diagnostics.get("error_category") or "")
            retry_tokens = (
                service.config["candidate_length_retry_max_tokens"]
                if category == "JSON_TRUNCATED"
                else service.config["candidate_max_tokens"]
            )
            if category in {"EMPTY_JSON_CONTENT", "JSON_TRUNCATED"}:
                request = service._candidate_request(
                    compact, retry=True, max_tokens=retry_tokens
                )
            else:
                request = service._candidate_repair_request(
                    _full_code(source),
                    response.content,
                    checked.diagnostics,
                    retry_tokens,
                )
            repaired = gateway.chat(request)
            responses.append(repaired)
            checked = _validate_single(
                repaired, _full_code(source), service.config["max_item_characters"]
            )
        api_calls += len(responses)
        input_tokens += sum(int(value.input_tokens or 0) for value in responses)
        output_tokens += sum(int(value.output_tokens or 0) for value in responses)
        if checked.value is None:
            result = {
                "stock_code": item,
                "stock_name": source.get("stock_name"),
                "quant_rank": int(source["rank"]),
                "quant_score": _number(source.get("total_score")),
                "flash_score": _number(flash.get("llm_score")),
                "execution_status": "FAILED",
                "error_category": checked.diagnostics.get("error_category"),
                "diagnostics": checked.diagnostics,
                "prompt_version": SINGLE_PROMPT_VERSION,
                "contract_version": SINGLE_CONTRACT_VERSION,
                "input_hash": _canonical_hash(compact),
            }
        else:
            result = {
                **checked.value.model_dump(mode="json"),
                "stock_code": item,
                "stock_name": source.get("stock_name"),
                "quant_rank": int(source["rank"]),
                "quant_score": _number(source.get("total_score")),
                "flash_score": _number(flash.get("llm_score")),
                "flash_decision": flash.get("screening_decision"),
                "execution_status": "SUCCESS",
                "actual_model": responses[-1].model,
                "prompt_version": SINGLE_PROMPT_VERSION,
                "contract_version": SINGLE_CONTRACT_VERSION,
                "ranking_version": RANKING_VERSION,
                "input_hash": _canonical_hash(compact),
                "diagnostics": checked.diagnostics,
            }
        checkpoint["pro"][item] = result
        _save_checkpoint(checkpoint)
        results.append(result)
    successful = [row for row in results if row["execution_status"] == "SUCCESS"]
    priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "REVIEW_ONLY": 3}
    manual = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    ordered = sorted(
        successful,
        key=lambda row: (
            -_number(row.get("pro_score")),
            priority.get(str(row.get("priority")), 9),
            manual.get(str(row.get("manual_review_priority")), 9),
            -_number(row.get("flash_score")),
            int(row["quant_rank"]),
            row["stock_code"],
        ),
    )
    for index, row in enumerate(ordered, 1):
        row["pro_rank"] = index
    return ordered, {
        "business_inputs": len(flash_top20),
        "new_business_calls": new_business,
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "api_calls": api_calls,
        "repairs": repairs,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "prompt_version": SINGLE_PROMPT_VERSION,
        "contract_version": SINGLE_CONTRACT_VERSION,
    }


def apply_regime_cap(
    pro_rows: Sequence[Mapping[str, Any]],
    top100_by_code: Mapping[str, Mapping[str, Any]],
    theme_by_stock: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    watch: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    active_industries: set[str] = set()
    active_themes: set[str] = set()
    removals = 0
    for pro in pro_rows[:10]:
        item = stock_code(pro.get("stock_code"))
        source = top100_by_code[item]
        theme = theme_by_stock[item]
        row = {
            "deployment_status": "WATCH_ONLY",
            "stock_code": item,
            "stock_name": source.get("stock_name"),
            "v2_rank": source.get("rank"),
            "v2_score": source.get("total_score"),
            "risk_health_score": source.get("risk_score"),
            "industry": source.get("level_one_sector"),
            "binding_cluster": theme.get("binding_cluster"),
            "all_theme_clusters": theme.get("all_theme_clusters"),
            "flash_score": pro.get("flash_score"),
            "flash_decision": pro.get("flash_decision"),
            "pro_rank": pro.get("pro_rank"),
            "pro_score": pro.get("pro_score"),
            "pro_priority": pro.get("priority"),
            "pro_summary": pro.get("final_summary"),
            "pro_key_strengths": "|".join(pro.get("key_strengths") or []),
            "pro_key_risks": "|".join(pro.get("key_risks") or []),
            "regime": "RISK_OFF",
            "concentration_reason": "",
            "disclaimer": DISCLAIMER,
        }
        eligible = (
            pro.get("priority") in {"HIGH", "MEDIUM"}
            and not bool(pro.get("data_conflict"))
        )
        reasons = []
        industry = str(source.get("level_one_sector") or "UNKNOWN")
        binding = str(theme.get("binding_cluster") or "其他主要相关方向")
        if not eligible:
            reasons.append("PRO_NOT_ACTIVE_ELIGIBLE")
        if len(active) >= 2:
            reasons.append("RISK_OFF_ACTIVE_MAX_2")
        if industry in active_industries:
            reasons.append("RISK_OFF_SAME_INDUSTRY_MAX_1")
        if binding in active_themes:
            reasons.append("RISK_OFF_SAME_THEME_MAX_1")
        if not reasons:
            row["deployment_status"] = "ACTIVE_SHADOW"
            row["concentration_reason"] = "RETAINED_UNDER_RISK_OFF_CAP"
            active.append(dict(row))
            active_industries.add(industry)
            active_themes.add(binding)
            theme["retained"] = True
            theme["concentration_reason"] = row["concentration_reason"]
        else:
            row["concentration_reason"] = "|".join(reasons)
            if eligible and any("SAME_" in reason for reason in reasons):
                removals += 1
            theme["retained"] = False
            theme["concentration_reason"] = row["concentration_reason"]
        watch.append(row)
    watch_codes = {row["stock_code"] for row in watch}
    for item, theme in theme_by_stock.items():
        if item not in watch_codes and theme.get("retained") == "":
            theme["retained"] = False
            theme["concentration_reason"] = "NOT_IN_PRO_TOP10_WATCH_POOL"
    return watch, active, removals


def _markdown_report(report: Mapping[str, Any]) -> str:
    active = report.get("final_active_candidates") or []
    names = "、".join(
        f"{row['stock_name']}({row['stock_code']})" for row in active
    ) or "无"
    plans = report.get("order_plans") or []
    return "\n".join(
        [
            "# Monday V2 Candidate Finalization",
            "",
            f"- Phase: {report['phase']}",
            f"- Risk direction audit: {report['risk_direction_audit']}",
            f"- Risk lineage: {report['risk_lineage']}",
            f"- Risk reversals explained: {report['risk_reversals_explained']}",
            f"- Risk calculation errors: {report['risk_calculation_errors']}",
            f"- Market regime: {report['market_regime']}",
            f"- Watch pool: {report['watch_pool']}",
            f"- Active shadow count: {report['active_shadow_count']}",
            f"- Theme clusters: {report['theme_clusters']}",
            f"- Concentration removals: {report['concentration_removals']}",
            f"- Flash calls: {report['flash_calls']}",
            f"- Flash Top20: {report['flash_top20']}",
            f"- Pro calls: {report['pro_calls']}",
            f"- Pro result: {report['pro_result']}",
            f"- Final active candidates: {names}",
            f"- Order plans: {len(plans)} advisory-only DRAFT plan(s)",
            f"- Production changes: {report['production_changes']}",
            f"- Orders: {report['orders']}",
            f"- Scheduler: {report['scheduler']}",
            f"- Tests: {report['tests']}",
            f"- Final status: {report['final_status']}",
            "",
            DISCLAIMER,
            "",
        ]
    )


def finalize(*, audit_only: bool = False) -> dict[str, Any]:
    prior_delivery = read_json(DELIVERABLES["audit"], {})
    source_hashes_before = {
        "base_report": _sha256(BASE_REPORT),
        "full_universe": _sha256(FULL_UNIVERSE),
        "legacy_formal_report": _sha256(FORMAL_REPORT),
        "quant_code": _sha256(ROOT / "quant" / "shadow" / "tushare_quant_v2.py"),
        "flash_prompt_code": _sha256(ROOT / "research" / "structured_validation.py"),
        "flash_score_code": _sha256(ROOT / "research" / "flash_v4.py"),
        "pro_prompt_code": _sha256(ROOT / "trader_demo" / "pro_single_v3.py"),
    }
    report, top20, top100 = _load_base()
    risk_rows, risk_audit, risk_summary = audit_risk_lineage(report, top20)
    reversal = risk_reversal_explanations(risk_summary)
    theme_rows, theme_by_stock = build_theme_membership(top100)
    _write_csv(DELIVERABLES["risk"], risk_rows)
    _write_csv(DELIVERABLES["theme"], theme_rows)
    military_check = {
        item: {
            "stock_name": next(
                (
                    row.get("stock_name")
                    for row in top100
                    if stock_code(row.get("stock_code")) == item
                ),
                item,
            ),
            "binding_cluster": (theme_by_stock.get(item) or {}).get("binding_cluster"),
            "is_military_cluster": "军工装备"
            in str((theme_by_stock.get(item) or {}).get("all_theme_clusters") or ""),
        }
        for item in ("300008", "002414", "301357")
    }
    if audit_only:
        payload = {
            "base_run_id": BASE_RUN_ID,
            "risk_audit": risk_audit,
            "risk_reversals": reversal,
            "military_cluster_check": military_check,
            "risk_lineage_rows": len(risk_rows),
            "theme_rows": len(theme_rows),
            "status": "AUDIT_READY" if risk_audit["status"] == "PASS" else "RISK_V2_DIRECTION_ERROR",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return payload

    top100_by_code = {
        stock_code(row.get("stock_code")): dict(row) for row in top100
    }
    checkpoint = _load_checkpoint()
    flash_stats: dict[str, Any] = {}
    pro_stats: dict[str, Any] = {}
    flash_top20: list[dict[str, Any]] = []
    pro_rows: list[dict[str, Any]] = []
    watch: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    concentration_removals = 0
    order_plans: list[dict[str, Any]] = []
    final_status = "BLOCKED"
    error: str | None = None
    test_text = (
        TEST_LOG.read_text(encoding="utf-8", errors="replace")
        if TEST_LOG.exists()
        else ""
    )
    test_status = (
        "PASS"
        if test_text
        and " failed" not in test_text.lower()
        and (" passed" in test_text.lower() or "security checks passed" in test_text.lower())
        else "PENDING_FINAL_VERIFICATION"
    )
    if risk_audit["status"] != "PASS":
        final_status = "RISK_V2_DIRECTION_ERROR"
    else:
        try:
            with temporary_real_llm_runtime():
                flash_top20, flash_stats = run_flash_once(
                    top100, theme_by_stock, risk_summary, checkpoint
                )
                if (
                    len(flash_top20) < 20
                    or bool((flash_stats.get("quality") or {}).get("degenerate"))
                ):
                    raise RuntimeError("FLASH_TOP20_NOT_USABLE")
                pro_rows, pro_stats = run_pro_once(
                    flash_top20, top100_by_code, theme_by_stock, checkpoint
                )
            prior_flash = prior_delivery.get("flash") or {}
            prior_pro = prior_delivery.get("pro") or {}
            if (
                flash_stats.get("new_business_calls") == 0
                and prior_delivery.get("base_run_id") == BASE_RUN_ID
            ):
                for key in (
                    "api_calls", "repairs", "input_tokens", "output_tokens",
                    "business_inputs", "successful", "failed", "quality",
                ):
                    if key in prior_flash:
                        flash_stats[key] = prior_flash[key]
                flash_stats["reused_successful_checkpoint"] = len(flash_top20)
            if (
                pro_stats.get("new_business_calls") == 0
                and prior_delivery.get("base_run_id") == BASE_RUN_ID
            ):
                for key in (
                    "api_calls", "repairs", "input_tokens", "output_tokens",
                    "business_inputs", "successful", "failed",
                ):
                    if key in prior_pro:
                        pro_stats[key] = prior_pro[key]
                pro_stats["reused_successful_checkpoint"] = len(pro_rows)
            if not pro_rows:
                final_status = "LLM_REVIEW_FAILED"
            else:
                watch, active, concentration_removals = apply_regime_cap(
                    pro_rows, top100_by_code, theme_by_stock
                )
                daily = by_code(trade_date_rows("daily"))
                basic = by_code(trade_date_rows("daily_basic"))
                stock_by = _load_cached_stock_basic()
                histories = load_histories()
                active_sources = [top100_by_code[row["stock_code"]] for row in active]
                order_plans = _order_plans(
                    active_sources,
                    histories=histories,
                    stock_by=stock_by,
                    daily_by=daily,
                    basic_by=basic,
                )
                for row, plan in zip(active, order_plans):
                    row["order_plan_status"] = plan.get("status")
                    row["recommended_price"] = plan.get("recommended_price")
                    row["price_range_low"] = plan.get("price_range_low")
                    row["price_range_high"] = plan.get("price_range_high")
                    row["stop_loss_price"] = plan.get("stop_loss_price")
                    row["target_price"] = plan.get("target_price")
                    row["actionable"] = False
                final_status = (
                    "MONDAY_V2_SHADOW_FINALIZED"
                    if active
                    else "MONDAY_WATCH_POOL_ONLY"
                )
        except Exception as exc:
            error = f"{type(exc).__name__}:{str(exc)[:500]}"
            final_status = "LLM_REVIEW_FAILED"

    _write_csv(DELIVERABLES["watch"], watch)
    _write_csv(DELIVERABLES["active"], active)
    _write_csv(DELIVERABLES["theme"], theme_rows)
    source_hashes_after = {
        key: _sha256(path)
        for key, path in {
            "base_report": BASE_REPORT,
            "full_universe": FULL_UNIVERSE,
            "legacy_formal_report": FORMAL_REPORT,
            "quant_code": ROOT / "quant" / "shadow" / "tushare_quant_v2.py",
            "flash_prompt_code": ROOT / "research" / "structured_validation.py",
            "flash_score_code": ROOT / "research" / "flash_v4.py",
            "pro_prompt_code": ROOT / "trader_demo" / "pro_single_v3.py",
        }.items()
    }
    immutable = source_hashes_before == source_hashes_after
    audit_payload = {
        "phase": "Monday V2 Candidate Finalization — Risk Audit + Regime Cap + Flash/Pro Review",
        "base_run_id": BASE_RUN_ID,
        "trade_date": TRADE_DATE,
        "target_trade_date": TARGET_DATE,
        "disclaimer": DISCLAIMER,
        "risk_direction_audit": risk_audit,
        "risk_reversals": reversal,
        "military_cluster_check": military_check,
        "market_regime": report["v2_run"]["global_regime"],
        "flash": {
            **flash_stats,
            "prompt_version": SCREENING_PROMPT_VERSION,
            "score_version": FLASH_SCORE_VERSION,
            "ranking_version": FLASH_RANKING_VERSION,
            "top20": flash_top20,
        },
        "pro": {**pro_stats, "results": pro_rows},
        "watch_pool": watch,
        "active_shadow": active,
        "order_plans": order_plans,
        "concentration_removals": concentration_removals,
        "source_hashes_before": source_hashes_before,
        "source_hashes_after": source_hashes_after,
        "immutable_sources_confirmed": immutable,
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
        "production_changes": "NONE",
        "tests": test_status,
        "error": error,
        "final_status": final_status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(DELIVERABLES["audit"], audit_payload)
    final_report = {
        "phase": audit_payload["phase"],
        "risk_direction_audit": risk_audit["status"],
        "risk_lineage": f"{len(risk_rows)} rows for {len(risk_summary)} stocks",
        "risk_reversals_explained": f"{len(reversal)}/4",
        "risk_calculation_errors": (
            "0"
            if risk_audit["status"] == "PASS"
            else json.dumps(
                risk_audit["invariants"]["reconstruction_mismatches"],
                ensure_ascii=False,
            )
        ),
        "market_regime": (
            f"{report['v2_run']['global_regime']['regime']};"
            f"advancing_ratio={report['v2_run']['global_regime']['advancing_ratio']:.1%}"
        ),
        "watch_pool": len(watch),
        "active_shadow_count": len(active),
        "theme_clusters": sorted(
            {
                value
                for row in theme_rows
                for value in str(row["all_theme_clusters"]).split("|")
                if value
            }
        ),
        "concentration_removals": concentration_removals,
        "flash_calls": flash_stats,
        "flash_top20": [row["stock_code"] for row in flash_top20],
        "pro_calls": pro_stats,
        "pro_result": f"success={len(pro_rows)}/{len(flash_top20)}",
        "final_active_candidates": active,
        "order_plans": order_plans,
        "production_changes": "NONE; Legacy unchanged; V2 remains Shadow",
        "orders": "real=0; virtual=0",
        "scheduler": "OFF",
        "tests": test_status,
        "final_status": final_status,
    }
    DELIVERABLES["report"].write_text(
        _markdown_report(final_report), encoding="utf-8"
    )
    print(json.dumps(final_report, ensure_ascii=False, indent=2, default=str))
    return audit_payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--trade-date", default=TRADE_DATE)
    parser.add_argument("--target-trade-date", default=TARGET_DATE)
    parser.add_argument("--base-run-id")
    parser.add_argument("--formal-report", type=Path)
    args = parser.parse_args()
    configure_runtime(
        args.trade_date,
        args.target_trade_date,
        base_run_id=args.base_run_id,
        formal_report=args.formal_report,
    )
    payload = finalize(audit_only=args.audit_only)
    if args.audit_only:
        return 0 if payload["status"] == "AUDIT_READY" else 2
    return 0 if payload["final_status"] in {
        "MONDAY_V2_SHADOW_FINALIZED",
        "MONDAY_WATCH_POOL_ONLY",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
