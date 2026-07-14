from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from database.session import get_session, init_db
from scripts.run_guarded_llm_excel_validation import _secret_scan
from scripts.run_trader_demo_excel import _serialize_readback
from trader_demo.service import TraderDemoService


SAFE_RUNTIME = {
    "LLM_REAL_CALLS_ENABLED": "false",
    "RUN_REAL_FUNDAMENTAL_RESEARCH": "false",
    "LLM_GATEWAY_MOCK_ONLY": "true",
}

DECISIONS = {
    "ADVANCE": "优先复核",
    "HOLD": "继续观察",
    "WATCH_ONLY": "普通观察",
    "REJECT": "暂不考虑",
    "BLOCK": "规则阻断",
}
SOURCES = {"LLM_TOP20": "模型筛选", "MANUAL": "人工关注", "BOTH": "共同入选"}
PRIORITIES = {"HIGH": "高", "MEDIUM": "中", "LOW": "低", "REVIEW_ONLY": "仅复核"}
FINANCIAL = {"STABLE": "稳定", "PRESSURED": "承压", "NORMAL": "正常", "UNKNOWN": "信息不足"}
CHAIN_POSITIONS = {
    "UPSTREAM": "上游", "MIDSTREAM": "中游", "DOWNSTREAM": "下游",
    "SERVICE_PLATFORM": "服务平台", "MULTI_SEGMENT": "多环节", "UNKNOWN": "信息不足",
}
ORDER_STATUS = {"DRAFT": "已生成", "BLOCKED": "已阻断", "NEEDS_REVIEW": "需复核"}
POSITION_STATUS = {"NON_ACTIONABLE": "模拟建议", "BLOCKED": "已阻断", "DRAFT": "已生成"}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成按交易日归档的中文人工阅读版工作簿")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--validation-run", default="")
    parser.add_argument("--use-existing", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.date).isoformat()
    _assert_safe_runtime()

    checkpoint_path = _find_checkpoint(trade_date)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    validation_run_id = args.validation_run or str(checkpoint.get("flash_v4_run_id") or "")
    if not validation_run_id:
        raise ValueError("FLASH_VALIDATION_RUN_ID_REQUIRED")

    init_db()
    session = get_session()
    try:
        raw = _serialize_readback(TraderDemoService(session).readback(validation_run_id))
    finally:
        session.close()
    payload = build_human_payload(raw, trade_date)
    if not _secret_scan(json.dumps(payload, ensure_ascii=False, default=str)):
        raise ValueError("HUMAN_OUTPUT_SECRET_SCAN_FAILED")

    daily_root = (ROOT / "outputs" / trade_date).resolve()
    output_root = (ROOT / "outputs").resolve()
    if output_root not in daily_root.parents:
        raise ValueError("DAILY_OUTPUT_PATH_OUTSIDE_OUTPUTS")
    daily_root.mkdir(parents=True, exist_ok=True)
    output_path = daily_root / f"智能交易助手_{trade_date}.xlsx"
    if output_path.exists() and args.replace_existing:
        history_dir = daily_root / "历史版本"
        history_dir.mkdir(parents=True, exist_ok=True)
        old_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()[:8]
        output_path.replace(history_dir / f"{output_path.stem}_修订前_{old_hash}.xlsx")
    if output_path.exists() and not args.use_existing:
        raise FileExistsError(f"OUTPUT_EXISTS:{output_path.name}")
    preview_dir = daily_root / "预览"
    if not output_path.exists():
        _build_workbook(payload, output_path, preview_dir)
    validation = _validate_workbook(output_path)
    workbook_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()

    audit_dir = daily_root / "审计"
    audit_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "交易日": trade_date,
        "生成状态": "完成",
        "数据来源运行": validation_run_id,
        "工作簿": str(output_path),
        "工作簿摘要": workbook_hash,
        "候选数量": len(payload["candidates"]),
        "非零仓位数量": sum(int(row["建议股数"] or 0) > 0 for row in payload["orders"]),
        "当前问题数量": len(payload["issues"]),
        "检查结果": validation,
        "外部模型调用": 0,
        "外部行情调用": 0,
    }
    manifest_path = audit_dir / "人工阅读版_生成记录.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def build_human_payload(raw: dict[str, Any], trade_date: str) -> dict[str, Any]:
    llm_by_code = {row["stock_code"]: row for row in raw["llm_rows"]}
    fundamental_by_code = {row["stock_code"]: row for row in raw["fundamental_rows"]}
    candidates = []
    orders = []
    fundamentals = []
    for order in raw["order_rows"]:
        code = order["stock_code"]
        llm = llm_by_code.get(code, {})
        fundamental = fundamental_by_code.get(code, {})
        common = {
            "股票代码": code,
            "股票名称": order["stock_name"],
            "入选来源": SOURCES.get(order["selection_source"], _human_text(order["selection_source"])),
            "量化排名": order["quant_rank"],
            "量化得分": order["quant_score"],
            "二筛得分": order["llm_score"],
            "二筛结论": DECISIONS.get(order["llm_decision"], _human_text(order["llm_decision"])),
            "深度复核分": order["pro_score"],
            "深度复核排名": order["pro_rank"],
            "复核优先级": PRIORITIES.get(order["pro_priority"], _human_text(order["pro_priority"])),
        }
        candidates.append({
            **common,
            "一级行业": _human_text(order["level_one_sector"]),
            "产业链": _human_text(fundamental.get("industry_chain")),
            "财务状态": FINANCIAL.get(order["financial_status"], _human_text(order["financial_status"])),
            "建议仓位": order["position_percent"],
            "建议股数": order["quantity"],
            "参考价": order["recommended_price"],
            "止损价": order["stop_loss_price"],
            "第二目标价": order["take_profit_2"],
            "风险收益比": order["active_risk_reward"],
            "核心逻辑": _human_text(fundamental.get("investment_logic")),
            "主要风险": _human_text(llm.get("risk_note")),
            "当前状态": ORDER_STATUS.get(order["order_status"], _human_text(order["order_status"])),
        })
        orders.append({
            **common,
            "保守价": order["conservative_price"], "均衡价": order["balanced_price"],
            "积极价": order["aggressive_price"], "参考价": order["recommended_price"],
            "最高接受价": order["max_acceptable_price"], "止损价": order["stop_loss_price"],
            "第一目标价": order["take_profit_1"], "第二目标价": order["take_profit_2"],
            "第一目标风险收益比": order["risk_reward_to_tp1"],
            "第二目标风险收益比": order["risk_reward_to_tp2"],
            "当前风险收益比": order["active_risk_reward"],
            "建议仓位": order["position_percent"], "建议资金": order["capital_amount"],
            "建议股数": order["quantity"], "预计最大损失": order["max_loss"],
            "挂单状态": ORDER_STATUS.get(order["order_status"], _human_text(order["order_status"])),
            "仓位状态": POSITION_STATUS.get(order["position_status"], _human_text(order["position_status"])),
            "说明": _human_warning(order["order_warning"], order["position_warning"]),
        })
        fundamentals.append({
            **common,
            "一级行业": _human_text(fundamental.get("level_one_sector")),
            "产业链": _human_text(fundamental.get("industry_chain")),
            "链条位置": CHAIN_POSITIONS.get(fundamental.get("chain_position"), _human_text(fundamental.get("chain_position"))),
            "主营业务": _human_text(fundamental.get("main_business")),
            "核心产品": _human_text(fundamental.get("core_products")),
            "概念标签": _human_text(fundamental.get("concept_tags")),
            "结构性方向": _human_text(fundamental.get("structural_theme_fit")),
            "潜在优势": _human_text(fundamental.get("competitive_advantage")),
            "行业趋势": _human_text(fundamental.get("industry_trend")),
            "核心逻辑": _human_text(fundamental.get("investment_logic")),
            "失效条件": _human_text(fundamental.get("logic_invalidation")),
            "财务状态": FINANCIAL.get(fundamental.get("financial_status"), _human_text(fundamental.get("financial_status"))),
            "财务说明": _human_text(fundamental.get("financial_status_reason")),
            "人工复核": _yes_no(fundamental.get("manual_review")),
        })
    candidates.sort(key=lambda row: (row["深度复核排名"] is None, row["深度复核排名"] or 9999, row["股票代码"]))
    orders.sort(key=lambda row: (row["深度复核排名"] is None, row["深度复核排名"] or 9999, row["股票代码"]))
    fundamentals.sort(key=lambda row: (row["深度复核排名"] is None, row["深度复核排名"] or 9999, row["股票代码"]))

    quant_top100 = [{
        "量化排名": row["rank"], "股票代码": row["stock_code"], "股票名称": row["stock_name"],
        "一级行业": _human_text(row["level_one_sector"]), "量化总分": row["total_score"],
        "技术得分": row["technical_score"], "资金得分": row["capital_score"],
        "情绪得分": row["emotion_score"], "动量得分": row["momentum_score"], "风险得分": row["risk_score"],
        "进入二筛": _yes_no(row["llm_evaluated"]), "进入重点候选": _yes_no(row["selection_source"]),
    } for row in raw["quant_rows"] if int(row["rank"]) <= 100]
    issues = [_human_issue(row, llm_by_code, order=False) for row in raw.get("errors") or []]
    issues.extend(_human_issue(row, llm_by_code, order=True) for row in raw.get("warnings") or [])
    run = raw["run"]
    return {
        "title": f"{trade_date} A股短线观察清单",
        "status_line": "供人工复核，最终交易决策由你确认。",
        "trade_date": trade_date,
        "target_date": run.get("target_market_trade_date") or run.get("target_trade_date") or "",
        "summary": {
            "量化股票数": len(raw["quant_rows"]), "二筛股票数": len(raw["llm_rows"]),
            "重点候选数": len(candidates), "非零仓位数": sum(int(row["建议股数"] or 0) > 0 for row in orders),
            "当前问题数": len(issues),
            "历史已解决问题数": sum(
                row.get("schema_status") == "RESOLVED_HISTORY" for row in raw.get("audits") or []
            ),
        },
        "top10": candidates[:10], "candidates": candidates, "orders": orders,
        "fundamentals": fundamentals, "quant_top100": quant_top100, "issues": issues,
    }


def _human_issue(row: dict[str, Any], llm_by_code: dict[str, dict], *, order: bool) -> dict[str, Any]:
    code = str(row.get("stock_code") or "")
    llm = llm_by_code.get(code, {})
    reason = str(row.get("reason") or "")
    if "no_unsupported_customer" in reason:
        reason = "内容包含未核验的客户信息，需要人工确认"
    elif "no_unsupported_leadership_claim" in reason:
        reason = "内容包含缺少依据的行业领先表述，需要人工确认"
    elif order:
        reason = (
            "基本面已完成外部资料核验；原挂单与仓位结果保持阻断，等待下游重新评估"
            if "EXTERNAL_FUNDAMENTAL_COMPLETED_DOWNSTREAM_REVIEW_PENDING" in reason
            else "基本面补全未通过，挂单和仓位建议已按规则阻断"
        )
    return {
        "股票代码": code, "股票名称": llm.get("stock_name") or "",
        "问题类型": "规则提醒" if order else "基本面补全",
        "当前状态": "已阻断" if order else "待人工确认",
        "说明": _human_text(reason),
    }


def _human_warning(*values: Any) -> str:
    text = "；".join(str(value or "") for value in values)
    replacements = {
        "仅供模型验证，不可作为正式交易仓位建议": "",
        "LLM_UNVERIFIED_POSITION_DISCOUNT_APPLIED": "未核验信息已按规则降权",
        "TARGET_DAY_LIMIT_RULE_ESTIMATED": "目标日涨跌停价为规则估算",
        "UNVERIFIED_FUNDAMENTAL_RESEARCH": "基本面信息含未核验推断",
        "BELOW_ONE_TRADING_LOT": "建议金额不足一手",
        "RISK_GATE_BLOCKED_OR_NO_RECOMMENDED_PRICE": "风险规则阻断或缺少有效参考价",
        "人工选择，但LLM未入选": "人工关注股，未进入模型重点名单",
        "人工选择，但LLM分析失败": "基本面补全未通过",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    parts = [part.strip() for part in re.split(r"[；;]", text) if part.strip()]
    return "；".join(dict.fromkeys(parts))


def _human_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    replacements = {
        "UNKNOWN": "信息不足", "INSUFFICIENT_DATA": "信息不足", "STABLE": "稳定",
        "PRESSURED": "承压", "NORMAL_WATCH": "普通观察", "KEY_WATCH": "重点观察",
        "WATCH_ONLY": "普通观察", "ADVANCE": "优先复核", "HOLD": "继续观察",
        "REJECT": "暂不考虑", "BLOCK": "规则阻断",
        "LLM": "模型", "MODEL_VALIDATION": "", "NON_ACTIONABLE": "",
        "MULTI_SEGMENT": "多环节", "MIDSTREAM": "中游", "UPSTREAM": "上游",
        "DOWNSTREAM": "下游", "SERVICE_PLATFORM": "服务平台",
        "Fundamental inference confidence low; industry position unclear; requires manual review.": "基本面推断置信度较低，行业位置尚不清晰，需要人工复核。",
        "Financial pressure and low confidence in fundamental inference require manual review.": "财务表现承压且基本面推断置信度较低，需要人工复核。",
        "Chip Probing": "晶圆测试", "FinalTest": "成品测试", "SoC": "系统级芯片",
        "GPU": "图形处理芯片", "PVC": "聚氯乙烯", "ROE": "净资产收益率",
        "LCD": "液晶显示面板", "LCM/TFT": "液晶显示模组/薄膜晶体管",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"^\d{6}：", "", text)
    text = re.sub(r"：P：stock_company：main_business$", "", text)
    return text.strip(" ；;")


def _yes_no(value: Any) -> str:
    if value in {True, "是", "true", "TRUE", 1}:
        return "是"
    if value in {False, None, "", "否", "false", "FALSE", 0}:
        return "否"
    return "是"


def _find_checkpoint(trade_date: str) -> Path:
    candidates = [
        ROOT / "outputs" / trade_date / "审计" / "flash_v4_checkpoint.json",
        ROOT / "outputs" / "daily_full_test" / "flash_v4_checkpoint.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("FLASH_V4_CHECKPOINT_NOT_FOUND")


def _build_workbook(payload: dict[str, Any], output_path: Path, preview_dir: Path) -> None:
    build_dir = output_path.parent / f".human-build-{uuid.uuid4().hex}"
    build_dir.mkdir(parents=True, exist_ok=False)
    link = build_dir / "node_modules"
    try:
        builder = build_dir / "build_human_daily_excel.mjs"
        shutil.copy2(ROOT / "scripts" / "build_human_daily_excel.mjs", builder)
        shutil.copy2(ROOT / "scripts" / "excel_alignment.mjs", build_dir / "excel_alignment.mjs")
        payload_path = build_dir / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        dependency_root = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
        if not (dependency_root / "@oai" / "artifact-tool").exists():
            raise RuntimeError("ARTIFACT_TOOL_DEPENDENCY_NOT_FOUND")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(dependency_root)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("ARTIFACT_TOOL_JUNCTION_FAILED")
        completed = subprocess.run(
            ["node", str(builder), str(payload_path), str(output_path), str(preview_dir)],
            cwd=build_dir, check=False,
        )
        if completed.returncode != 0 and not output_path.exists():
            raise RuntimeError(f"HUMAN_WORKBOOK_EXPORT_FAILED:{completed.returncode}")
    finally:
        if link.exists():
            os.rmdir(link)
        shutil.rmtree(build_dir, ignore_errors=True)


def _validate_workbook(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError("HUMAN_WORKBOOK_MISSING")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("HUMAN_WORKBOOK_ZIP_ERROR")
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8", errors="replace")
        sheet_count = len(re.findall(r"<(?:\w+:)?sheet\b", workbook_xml))
        if sheet_count not in {5, 6}:
            raise ValueError("HUMAN_WORKBOOK_SHEET_COUNT_ERROR")
        xml = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist() if name.endswith(".xml")
        )
    if not _secret_scan(xml):
        raise ValueError("HUMAN_WORKBOOK_SECRET_SCAN_FAILED")
    forbidden = ("MODEL_VALIDATION", "NON_ACTIONABLE", "WATCH_ONLY", "ADVANCE", "request_hash", "reasoning_content")
    if any(value.lower() in xml.lower() for value in forbidden):
        raise ValueError("HUMAN_WORKBOOK_TECHNICAL_TEXT_LEAK")
    return {"状态": "通过", "工作表数量": sheet_count, "压缩包完整性": "通过", "敏感信息检查": "通过"}


def _assert_safe_runtime() -> None:
    current = {key: os.getenv(key, default) for key, default in SAFE_RUNTIME.items()}
    if current != SAFE_RUNTIME or os.getenv("ENABLE_REAL_TRADING", "false").lower() != "false":
        raise RuntimeError("UNSAFE_RUNTIME_SWITCHES")


if __name__ == "__main__":
    raise SystemExit(main())
