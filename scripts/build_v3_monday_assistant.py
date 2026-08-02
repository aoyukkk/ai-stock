from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from event_overlay.forward_ab import apply_shadow_deployment_limits
from fundamentals.pipeline import CachedTushareProfileService
from reporting.immutable_workbook import workbook_content_style_hashes
from reporting.web_result_publish import publish_workbook_payload
from scripts.build_human_daily_output import (
    _build_workbook,
    _polish_workbook,
    _validate_workbook,
)
from scripts.build_v2_corrected_human_daily_output import (
    _candidate_prices,
    _code,
    _fundamental_status,
    _number,
    _repair_export_encoding,
    _text_list,
)
from scripts.run_tushare_quant_v2_validation import (
    _order_plans,
    by_code,
    configure_runtime,
    load_histories,
    trade_date_rows,
)


FACTOR_VERSION = "TUSHARE_QUANT_V3_1_EVENT_OVERLAY_SHADOW"
QUANT_FACTOR_VERSION = "TUSHARE_QUANT_V2_CORRECTED_SHADOW"
RECOMMENDATION_THRESHOLD = 80.0
FOCUS_COUNT = 20
ACCOUNT_CAPITAL = 100_000.0
POSITION_CAPITAL = 10_000.0


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _clean(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text if text and "�" not in text else fallback


def _score_eligible_events(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = [
        dict(item)
        for item in (snapshot.get("items") or [])
        if isinstance(item, Mapping)
    ]
    # V3.1 keeps rejected evidence for audit.  Once the eligibility field is
    # present, workbook-facing summaries must never fall back to those stale,
    # future, source-less or otherwise ineligible rows.  Older V3 snapshots
    # without the field retain their historical behavior.
    if any("score_eligible" in item for item in items):
        return [item for item in items if item.get("score_eligible") is True]
    return items


def _core_event(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    items = _score_eligible_events(snapshot)
    if not items:
        return {}
    return max(
        items,
        key=lambda item: float(item.get("materiality") or 0)
        * float(item.get("relevance") or 0)
        * float(item.get("confidence") or 0),
    )


def _event_summary(snapshot: Mapping[str, Any]) -> str:
    event = _core_event(snapshot)
    title = _clean(event.get("title"))
    summary = _clean(event.get("summary"))
    if title and summary and summary not in title:
        return f"{title}；{summary}"
    return title or summary or "V3.1事件层未发现合格的新消息，保持Quant原分并保留基本面复核。"


def _risk_summary(snapshot: Mapping[str, Any], risk_action: str) -> str:
    negatives = [
        _clean(item.get("title") or item.get("summary"))
        for item in _score_eligible_events(snapshot)
        if str(item.get("event_direction") or "").upper() == "NEGATIVE"
    ]
    negatives = [item for item in negatives if item]
    if negatives:
        return "；".join(negatives[:4])
    action = str(risk_action or "NEUTRAL").upper()
    if action == "PENALIZE":
        return "事件风险层给出扣分，需核验最新公告、可交易性和开盘价格偏离。"
    return "开盘前仍需核验停复牌、涨跌停、集合竞价偏离及盘前新增公告。"


def _priority(v3_rank: int, risk_action: str) -> str:
    if str(risk_action).upper() == "BLOCK":
        return "低"
    if v3_rank <= 10:
        return "高"
    return "中"


def _decision(risk_action: str) -> str:
    return {
        "PROMOTE": "优先复核",
        "NEUTRAL": "继续观察",
        "PENALIZE": "谨慎复核",
        "BLOCK": "规则阻断",
    }.get(str(risk_action or "").upper(), "继续观察")


def _deployment(
    top20: list[dict[str, Any]], market_regime: str
) -> tuple[set[str], set[str]]:
    result = apply_shadow_deployment_limits(
        top20,
        market_regime=market_regime,
        max_active=2,
    )
    active = {_code(row.get("stock_code")) for row in result["active_shadow"]}
    watch = {_code(row.get("stock_code")) for row in top20} - active
    return active, watch


def _order_plan_rows(
    trade_date: date,
    target_date: date,
    top20: list[dict[str, Any]],
    quant_by_code: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    configure_runtime(trade_date.isoformat(), target_date.isoformat())
    histories = load_histories()
    daily_by = by_code(trade_date_rows("daily"))
    basic_by = by_code(trade_date_rows("daily_basic"))
    usable: list[dict[str, Any]] = []
    stock_by: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for candidate in top20:
        code = candidate["stock_code"]
        quant = dict(quant_by_code.get(code) or {})
        quant.update(
            {
                "stock_code": code,
                "stock_name": candidate["stock_name"],
                "total_score": candidate["quant_score"],
            }
        )
        missing = [
            label
            for label, source in (
                ("daily", daily_by),
                ("daily_basic", basic_by),
                ("history", histories),
            )
            if code not in source
        ]
        if missing:
            errors.append(
                {
                    "stock_code": code,
                    "error": "MISSING_" + "_".join(missing).upper(),
                }
            )
            continue
        usable.append(quant)
        stock_by[code] = {
            "name": candidate["stock_name"],
            "industry": candidate["industry"],
        }
    plans = _order_plans(
        usable,
        histories=histories,
        stock_by=stock_by,
        daily_by=daily_by,
        basic_by=basic_by,
    )
    return plans, errors


def _profile_main_business(profile: Mapping[str, Any]) -> str:
    rows = profile.get("main_business") or []
    values = []
    for row in rows:
        value = _clean((row or {}).get("bz_item"))
        if value and value not in values:
            values.append(value)
    if values:
        return "；".join(values[:6])
    company = profile.get("company_profile") or {}
    return _clean(
        company.get("main_business")
        or company.get("business_scope")
        or company.get("introduction"),
        "结构化主营资料暂不完整，需结合公司公告复核。",
    )


def _financial_summary(profile: Mapping[str, Any]) -> str:
    values = profile.get("financial_summary") or {}
    labels = (
        ("end_date", "报告期"),
        ("revenue", "营业收入"),
        ("net_profit", "归母净利润"),
        ("gross_margin", "毛利率"),
        ("net_margin", "净利率"),
        ("debt_to_assets", "资产负债率"),
        ("operating_cash_flow", "经营现金流"),
    )
    parts: list[str] = []
    for key, label in labels:
        value = values.get(key)
        if value in (None, ""):
            continue
        if key in {"gross_margin", "net_margin", "debt_to_assets"}:
            number = _number(value)
            text = f"{number:.2f}%" if number is not None else str(value)
        elif key in {"revenue", "net_profit", "operating_cash_flow"}:
            number = _number(value)
            if number is None:
                text = str(value)
            elif abs(number) >= 100_000_000:
                text = f"{number / 100_000_000:.2f}亿元"
            elif abs(number) >= 10_000:
                text = f"{number / 10_000:.2f}万元"
            else:
                text = f"{number:.2f}元"
        else:
            text = str(value)
        parts.append(f"{label}{text}")
    if parts:
        return "；".join(parts) + "。"
    status = profile.get("financial_status") or {}
    reasons = status.get("reasons") or status.get("evidence_fields") or []
    reason_text = _text_list(reasons, limit=6)
    return reason_text or "结构化财务数据覆盖有限，保持待核验，不据此提高评分。"


def _load_existing_fundamentals(trade_date: date) -> dict[str, dict[str, Any]]:
    path = ROOT / "outputs" / trade_date.isoformat() / "审计" / (
        f"基本面LLM补全_V2_{trade_date:%Y%m%d}.json"
    )
    if not path.exists():
        return {}
    audit = read_json(path)
    return {
        _code(row.get("stock_code")): row
        for row in (audit.get("rows") or [])
        if row.get("status") == "COMPLETE"
    }


def _build_fundamental_rows(
    trade_date: date,
    decision_time: datetime,
    top20: list[dict[str, Any]],
    snapshots: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing = _load_existing_fundamentals(trade_date)
    profile_service = CachedTushareProfileService()
    workbook_rows: list[dict[str, Any]] = []
    publish_rows: list[dict[str, Any]] = []
    for candidate in top20:
        code = candidate["stock_code"]
        prior = existing.get(code) or {}
        profile = dict(prior.get("profile") or {})
        inference = dict(prior.get("inference") or {})
        source_label = "LLM补全"
        if not profile:
            profile = profile_service.build(
                code,
                decision_time=decision_time,
            ).model_dump(mode="json")
            source_label = "Tushare结构化资料"
        sector = _clean(
            candidate.get("industry") or profile.get("level_one_sector"),
            "待核验",
        )
        main_business = _clean(
            inference.get("main_business"),
            _profile_main_business(profile),
        )
        core_products = _text_list(
            inference.get("core_products") or profile.get("core_products"),
            limit=8,
        ) or "主营资料见公司结构化经营范围"
        concepts = _text_list(
            inference.get("concept_tags") or profile.get("normalized_concept_tags"),
            limit=8,
        ) or sector
        finance_status_raw = (
            inference.get("financial_status")
            or (profile.get("financial_status") or {}).get("status")
        )
        finance_status = _fundamental_status(finance_status_raw)
        logic = _clean(
            inference.get("investment_logic"),
            _event_summary(snapshots.get(code) or {}),
        )
        risk = _text_list(inference.get("invalidation_conditions"), limit=8) or _risk_summary(
            snapshots.get(code) or {}, candidate["risk_action"]
        )
        workbook_rows.append(
            {
                "深度复核排名": candidate["v3_rank"],
                "股票代码": code,
                "股票名称": candidate["stock_name"],
                "入选来源": "模型筛选",
                "一级行业": sector,
                "产业链": _clean(inference.get("industry_chain"), sector),
                "链条位置": _clean(inference.get("chain_position"), "多环节"),
                "主营业务": main_business,
                "核心产品": core_products,
                "概念标签": concepts,
                "结构性方向": _clean(
                    inference.get("structural_direction"),
                    candidate.get("binding_cluster") or sector,
                ),
                "潜在优势": _clean(
                    inference.get("competitive_advantage"),
                    "量化与V3.1合格事件层共同进入前20，仍需结合公告验证可持续性。",
                ),
                "行业趋势": _clean(
                    inference.get("industry_trend"),
                    f"{sector}方向，需继续跟踪行业景气与政策变化。",
                ),
                "核心逻辑": logic,
                "失效条件": risk,
                "财务状态": finance_status,
                "财务说明": _clean(
                    inference.get("financial_summary"),
                    _financial_summary(profile),
                ),
                "人工复核": source_label,
                "复核优先级": candidate["priority"],
                "核验来源": (
                    "Tushare结构化公司、主营和财务数据；"
                    "V3.1 Flash直接搜索合格事件证据快照"
                ),
            }
        )
        publish_rows.append(
            {
                "stock_code": code,
                "stock_name": candidate["stock_name"],
                "selection_source": "V3_1_TOP20",
                "status": "COMPLETE",
                "profile": profile,
                "inference": {
                    "main_business": main_business,
                    "core_products": core_products.split("；"),
                    "concept_tags": concepts.split("；"),
                    "industry_chain": workbook_rows[-1]["产业链"],
                    "chain_position": workbook_rows[-1]["链条位置"],
                    "structural_direction": workbook_rows[-1]["结构性方向"],
                    "competitive_advantage": workbook_rows[-1]["潜在优势"],
                    "industry_trend": workbook_rows[-1]["行业趋势"],
                    "investment_logic": logic,
                    "invalidation_conditions": risk.split("；"),
                    "financial_status": finance_status_raw or "UNKNOWN",
                    "financial_summary": workbook_rows[-1]["财务说明"],
                },
                "usage": prior.get("usage") or {"cached": True},
            }
        )
    return workbook_rows, publish_rows


def _universe_issues(
    quant_audit: Mapping[str, Any],
    top20: list[dict[str, Any]],
    plan_errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    names = {row["stock_code"]: row["stock_name"] for row in top20}
    issues: list[dict[str, Any]] = []
    for error in plan_errors:
        code = _code(error.get("stock_code"))
        issues.append(
            {
                "股票代码": code,
                "股票名称": names.get(code, code),
                "问题类型": "价格计划数据不足",
                "当前状态": "仅观察、不生成挂单",
                "说明": str(error.get("error") or "ORDER_PLAN_DATA_MISSING"),
            }
        )
    exclusions = (
        ((quant_audit.get("universe") or {}).get("unscored_trade_date_securities") or [])
    )
    for row in exclusions:
        code = _code(row.get("stock_code"))
        name = _clean(row.get("stock_name"), code)
        reason = _clean(row.get("reason"), "UNIVERSE_EXCLUSION")
        list_date = _clean(row.get("list_date"), "未知")
        history_count = int(row.get("history_count") or 0)
        if "ST" in name.upper():
            issue_type = "V3.1硬门禁"
            status = "已阻断、保留审计"
        elif reason in {"DATA_INSUFFICIENT_NEW_LISTING", "DATA_INSUFFICIENT_HISTORY"}:
            issue_type = "新股历史数据不足"
            status = "未评分、等待数据成熟"
        else:
            issue_type = "全A名单差异"
            status = "未评分、保留审计"
        issues.append(
            {
                "股票代码": code,
                "股票名称": name,
                "问题类型": issue_type,
                "当前状态": status,
                "说明": (
                    f"{reason}；上市日期={list_date}；可用日线={history_count}天。"
                    "未进入本次V3.1可评分集合，不补造分数或收益。"
                ),
            }
        )
    return issues


def _payload(
    trade_date: date,
    target_date: date,
    manifest: Mapping[str, Any],
    top20: list[dict[str, Any]],
    full_quant_rows: list[dict[str, Any]],
    snapshots: Mapping[str, Mapping[str, Any]],
    plans: list[dict[str, Any]],
    plan_errors: list[dict[str, Any]],
    fundamental_rows: list[dict[str, Any]],
    quant_audit: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if len(top20) != FOCUS_COUNT:
        raise RuntimeError(f"V3_TOP20_INCOMPLETE:{len(top20)}")
    if len(full_quant_rows) < 100:
        raise RuntimeError("V2_FULL_QUANT_UNIVERSE_INCOMPLETE")
    if any(row["v3_screening_score"] is None for row in top20):
        raise RuntimeError("V3_SCORE_MISSING")

    quant_by_code = {row["stock_code"]: row for row in full_quant_rows}
    plan_by_code = {_code(row.get("stock_code")): row for row in plans}
    active_codes, watch_codes = _deployment(top20, str(manifest["market_regime"]))
    candidates: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fundamentals_by_code = {row["股票代码"]: row for row in fundamental_rows}
    for candidate in top20:
        code = candidate["stock_code"]
        quant = quant_by_code.get(code) or {}
        plan = plan_by_code.get(code)
        prices = _candidate_prices(plan)
        position = 0.10 if code in active_codes else 0.0
        reference = prices["参考价"]
        shares = (
            math.floor(POSITION_CAPITAL / reference / 100.0) * 100
            if position and reference
            else 0
        )
        max_loss = (
            shares * max(0.0, reference - (prices["止损价"] or reference))
            if shares and reference
            else 0.0
        )
        fundamental = fundamentals_by_code[code]
        risk_text = _risk_summary(snapshots.get(code) or {}, candidate["risk_action"])
        logic = _event_summary(snapshots.get(code) or {})
        current_status = "ACTIVE_SHADOW" if code in active_codes else "重点观察"
        if candidate["risk_action"] == "BLOCK":
            current_status = "已阻断"
        row = {
            "深度复核排名": candidate["v3_rank"],
            "股票代码": code,
            "股票名称": candidate["stock_name"],
            "入选来源": "模型筛选",
            "量化排名": candidate["quant_rank"],
            "量化得分": candidate["quant_score"],
            "二筛得分": round(candidate["evidence_confidence"] * 100.0, 4),
            "二筛结论": _decision(candidate["risk_action"]),
            "深度复核分": candidate["v3_screening_score"],
            "复核优先级": candidate["priority"],
            "一级行业": candidate["industry"],
            "产业链": fundamental["产业链"],
            "财务状态": fundamental["财务状态"],
            "建议仓位": position,
            "建议股数": shares,
            "参考价": reference,
            "止损价": prices["止损价"],
            "第二目标价": prices["第二目标价"],
            "风险收益比": prices["当前收益比"],
            "核心逻辑": logic,
            "主要风险": risk_text,
            "当前状态": current_status,
        }
        candidates.append(row)
        note = (
            f"{manifest['market_regime']}下的ACTIVE_SHADOW；仅供人工复核，不创建订单。"
            if code in active_codes
            else f"{manifest['market_regime']}下仅观察，不配置仓位。"
        )
        if plan is None:
            note += " Order Price Engine未形成合法价格计划。"
        orders.append(
            {
                "深度复核排名": row["深度复核排名"],
                "股票代码": code,
                "股票名称": row["股票名称"],
                "入选来源": row["入选来源"],
                "量化得分": row["量化得分"],
                "二筛得分": row["二筛得分"],
                "二筛结论": row["二筛结论"],
                "深度复核分": row["深度复核分"],
                **prices,
                "建议仓位": position,
                "建议资金": POSITION_CAPITAL if position else 0.0,
                "建议股数": shares,
                "预计最大损失": max_loss,
                "说明": note,
            }
        )

    recommendations = [
        row for row in candidates
        if row["深度复核分"] is not None
        and float(row["深度复核分"]) > RECOMMENDATION_THRESHOLD
    ]
    focus_codes = {row["股票代码"] for row in candidates}
    quant_top100 = []
    for raw in sorted(full_quant_rows, key=lambda row: int(row["rank"]))[:100]:
        code = _code(raw.get("stock_code"))
        quant_top100.append(
            {
                "量化排名": int(raw["rank"]),
                "股票代码": code,
                "股票名称": raw.get("stock_name") or code,
                "一级行业": raw.get("level_one_sector") or "待核验",
                "量化总分": _number(raw.get("total_score")),
                "技术得分": _number(raw.get("technical_score")),
                "资金得分": _number(raw.get("capital_score")),
                "情绪得分": _number(raw.get("emotion_score")),
                "动量得分": _number(raw.get("momentum_score")),
                "风险得分": _number(raw.get("risk_score")),
                "进入二筛": "是",
                "进入重点候选": "是" if code in focus_codes else "否",
            }
        )
    issues = _universe_issues(quant_audit, top20, plan_errors)
    payload = {
        "title": f"{target_date.isoformat()} A股短线观察清单",
        "status_line": (
            f"V3.1最新事件增强日线；因子版本：{FACTOR_VERSION}；"
            f"量化基线：{QUANT_FACTOR_VERSION}；源运行：{manifest['run_id']}；"
            "重点候选为V3.1前20，今日推荐为其中V3.1分数严格大于80分；"
            "供人工复核，最终交易决策由你确认。"
        ),
        "trade_date": trade_date.isoformat(),
        "target_date": target_date.isoformat(),
        "minimum_recommendation_score": RECOMMENDATION_THRESHOLD,
        "recommendation_operator": "STRICT_GREATER_THAN",
        "summary": {
            "量化股票数": len(full_quant_rows),
            "二筛股票数": 100,
            "重点候选数": len(candidates),
            "非零仓位数": len(active_codes),
            "非零仓位数量": len(active_codes),
            "今日推荐数": len(recommendations),
            "当前问题数": len(issues),
            "历史已解决问题数": 0,
        },
        "recommendations": recommendations,
        "top10": candidates[:10],
        "candidates": candidates,
        "orders": orders,
        "fundamentals": fundamental_rows,
        "quant_top100": quant_top100,
        "issues": issues,
    }
    audit = {
        "status": "V3_1_SMART_TRADING_ASSISTANT_READY",
        "trade_date": trade_date.isoformat(),
        "target_trade_date": target_date.isoformat(),
        "source_run_id": manifest["run_id"],
        "factor_version": FACTOR_VERSION,
        "quant_factor_version": QUANT_FACTOR_VERSION,
        "focus_rule": "V3_1_TOP20",
        "recommendation_rule": "V3_1_SCORE_STRICTLY_GREATER_THAN_80",
        "candidate_count": len(candidates),
        "recommendation_count": len(recommendations),
        "quant_top100_count": len(quant_top100),
        "active_shadow_count": len(active_codes),
        "active_shadow_codes": sorted(active_codes),
        "watch_count": len(watch_codes),
        "order_plan_count": len(plans),
        "order_plan_errors": plan_errors,
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
        "production_changes": False,
    }
    return payload, audit


def _parse_top20(
    rows: Iterable[Mapping[str, Any]],
    quant_by_code: Mapping[str, Mapping[str, Any]],
    snapshots: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in rows:
        code = _code(raw.get("stock_code"))
        quant = quant_by_code.get(code) or {}
        snapshot = snapshots.get(code) or {}
        event = _core_event(snapshot)
        result.append(
            {
                "v3_rank": int(raw["v3_rank"]),
                "stock_code": code,
                "stock_name": _clean(raw.get("stock_name"), code),
                "quant_rank": int(raw["quant_rank"]),
                "quant_score": float(raw["quant_score"]),
                "event_opportunity_score": float(raw["event_opportunity_score"]),
                "evidence_confidence": float(raw["evidence_confidence"]),
                "risk_action": str(raw.get("risk_action") or "NEUTRAL"),
                "v3_screening_score": float(raw["v3_screening_score"]),
                "industry": _clean(quant.get("level_one_sector"), "待核验"),
                "binding_cluster": _clean(
                    event.get("event_type"),
                    _clean(quant.get("level_one_sector"), "其他主要相关方向"),
                ),
                "priority": _priority(
                    int(raw["v3_rank"]), str(raw.get("risk_action") or "NEUTRAL")
                ),
            }
        )
    result.sort(key=lambda row: row["v3_rank"])
    return result


def _synthesized_final_audit(
    manifest: Mapping[str, Any],
    top20: list[dict[str, Any]],
) -> dict[str, Any]:
    flash = []
    pro = []
    for row in top20:
        flash.append(
            {
                "stock_code": row["stock_code"],
                "stock_name": row["stock_name"],
                "llm_score": row["evidence_confidence"] * 100.0,
                "screening_decision": row["risk_action"],
            }
        )
        pro.append(
            {
                "stock_code": row["stock_code"],
                "stock_name": row["stock_name"],
                "pro_rank": row["v3_rank"],
                "pro_score": row["v3_screening_score"],
                "flash_score": row["evidence_confidence"] * 100.0,
                "priority": row["priority"],
            }
        )
    return {
        "base_run_id": manifest["run_id"],
        "trade_date": manifest["trade_date"],
        "target_trade_date": manifest["target_trade_date"],
        "final_status": manifest["final_status"],
        "market_regime": {"regime": manifest["market_regime"]},
        "flash": {"top20": flash, "business_inputs": len(flash)},
        "pro": {"results": pro, "business_inputs": len(pro), "successful": len(pro), "failed": 0},
    }


def build(run_dir: Path, output_path: Path) -> dict[str, Any]:
    manifest = read_json(run_dir / "run_manifest.json")
    if manifest.get("final_status") != "V3_EVENT_OVERLAY_SHADOW_READY":
        raise RuntimeError("V3_RUN_NOT_READY")
    trade_date = date.fromisoformat(str(manifest["trade_date"]))
    target_date = date.fromisoformat(str(manifest["target_trade_date"]))
    quant_dir = ROOT / "outputs" / "quant_v2_validation" / trade_date.isoformat()
    full_quant_rows = read_csv(quant_dir / "v2_full_universe.csv")
    quant_audit = read_json(quant_dir / "quant_v2_validation.json")
    for row in full_quant_rows:
        row["stock_code"] = _code(row.get("stock_code"))
    quant_by_code = {row["stock_code"]: row for row in full_quant_rows}
    snapshots_raw = read_json(run_dir / "event_evidence_snapshot.json")
    snapshots = {_code(row.get("stock_code")): row for row in snapshots_raw}
    top20 = _parse_top20(
        read_csv(run_dir / "v3_screening_top20.csv"),
        quant_by_code,
        snapshots,
    )
    market_regime = str(
        ((manifest.get("market_regime") or {}).get("status"))
        if isinstance(manifest.get("market_regime"), Mapping)
        else manifest.get("market_regime")
        or "UNKNOWN"
    )
    manifest = {**manifest, "market_regime": market_regime}
    decision_time = datetime.fromisoformat(str(manifest["decision_as_of_time"]))
    plans, plan_errors = _order_plan_rows(
        trade_date,
        target_date,
        top20,
        quant_by_code,
    )
    fundamental_rows, publish_fundamental_rows = _build_fundamental_rows(
        trade_date,
        decision_time,
        top20,
        snapshots,
    )
    payload, audit = _payload(
        trade_date,
        target_date,
        manifest,
        top20,
        full_quant_rows,
        snapshots,
        plans,
        plan_errors,
        fundamental_rows,
        quant_audit,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    day_dir = output_path.parent.parent if output_path.parent.name == "正式日线" else output_path.parent
    formal_dir = day_dir / "正式日线"
    audit_dir = day_dir / "审计"
    formal_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    formal_target = formal_dir / output_path.name
    root_target = day_dir / output_path.name
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    build_root = Path(tempfile.mkdtemp(prefix="v3-old-format-", dir=audit_dir))
    generated = build_root / output_path.name
    preview_dir = audit_dir / f"v3_old_format_visual_qa_{timestamp}"
    try:
        _build_workbook(payload, generated, preview_dir)
        _repair_export_encoding(generated)
        _polish_workbook(generated)
        validation = _validate_workbook(generated)
        generated_hashes = workbook_content_style_hashes(generated)
        backups: list[str] = []
        for target, label in ((formal_target, "formal"), (root_target, "root")):
            if target.exists():
                backup = audit_dir / f"替换前V3表_{label}_{timestamp}.xlsx"
                shutil.copy2(target, backup)
                backups.append(str(backup))
            temporary = target.with_suffix(target.suffix + ".tmp")
            shutil.copy2(generated, temporary)
            os.replace(temporary, target)
    finally:
        shutil.rmtree(build_root, ignore_errors=True)

    formal_hashes = workbook_content_style_hashes(formal_target)
    root_hashes = workbook_content_style_hashes(root_target)
    if formal_hashes["file_sha256"] != root_hashes["file_sha256"]:
        raise RuntimeError("FORMAL_ROOT_WORKBOOK_HASH_MISMATCH")
    if formal_hashes["file_sha256"] != generated_hashes["file_sha256"]:
        raise RuntimeError("WORKBOOK_REPLACEMENT_HASH_MISMATCH")

    final_audit = _synthesized_final_audit(manifest, top20)
    web_publish = publish_workbook_payload(
        trade_date=trade_date,
        target_trade_date=target_date,
        payload=payload,
        full_quant_rows=full_quant_rows,
        final_audit=final_audit,
        fundamental_rows=publish_fundamental_rows,
        workbook_path=root_target,
        workbook_sha256=formal_hashes["file_sha256"],
        workbook_content_hash=formal_hashes["content_hash"],
        factor_version=FACTOR_VERSION,
        production_promoted=False,
    )

    deleted_superseded: list[str] = []
    for directory, retained in ((day_dir, root_target), (formal_dir, formal_target)):
        for path in directory.glob(f"智能交易助手*{target_date.isoformat()}*.xlsx"):
            if path.resolve() == retained.resolve():
                continue
            backup = audit_dir / f"替换前V3表_其他_{timestamp}_{path.name}"
            shutil.copy2(path, backup)
            path.unlink()
            backups.append(str(backup))
            deleted_superseded.append(str(path))

    audit.update(
        {
            "formal_workbook": str(formal_target.resolve()),
            "root_workbook": str(root_target.resolve()),
            "workbook_hashes": formal_hashes,
            "validation": validation,
            "visual_qa_dir": str(preview_dir.resolve()),
            "web_publish": web_publish,
            "backups": backups,
            "deleted_superseded": deleted_superseded,
            "workbook_sha256": file_hash(formal_target),
        }
    )
    audit_path = audit_dir / f"智能交易助手_V3_1修复版_{timestamp}_audit.json"
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    audit["audit_path"] = str(audit_path.resolve())
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.run_dir.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
