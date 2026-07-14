from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env", override=False)

from database.session import get_session, init_db
from research.knowledge_mode import LLMKnowledgeMode
from scripts.run_guarded_llm_excel_validation import _run_real_preflight, _secret_scan
from trader_demo.service import ManualSelection, TraderDemoService
from stock_codes import display_stock_code, normalize_ts_code


SHEET_NAMES = ["01_全A量化排名", "02_Top100_LLM评分", "03_挂单与仓位", "04_重点基本面", "05_说明与异常"]


def main() -> int:
    args = _parser().parse_args()
    try:
        _validate_args(args)
        manual, manual_source = _manual_selections(args)
        ranks = None if args.llm_top_n is not None else _parse_ranks(args.llm_ranks)
        retry_stocks = _parse_retry_stocks(args.retry_stocks)
        init_db()
        session = get_session()
        try:
            service = TraderDemoService(session)
            if args.validation_run:
                validation_run_id = args.validation_run
                preflight = {"status": "NOT_RUN", "reason": "DATABASE_READBACK_EXPORT_ONLY"}
            else:
                quant_run_id = None if args.quant_run == "latest-formal" else args.quant_run
                preview = service.preview(
                    quant_run_id=quant_run_id, ranks=ranks, top_n=args.llm_top_n, manual=manual,
                    retry_stocks=retry_stocks,
                )
                preview["manual_source"] = manual_source
                preview["expected_business_calls"] = 2 * preview["llm_evaluation_count"]
                preview["output_dir"] = str(Path(args.output_dir).resolve())
                if args.dry_run or not args.real_llm:
                    print(json.dumps(preview, ensure_ascii=False, indent=2, default=str))
                    return 0
                if not preview["real_gate_ready"]:
                    print(json.dumps({"status": "BLOCKED", "reason": "REAL_LLM_GUARDS_NOT_SATISFIED", "preview": preview}, ensure_ascii=False, indent=2))
                    return 2
                preflight = _run_real_preflight()
                if preflight["status"] != "PASS":
                    print(json.dumps({"status": "BLOCKED", "reason": preflight["reason"], "preflight": preflight}, ensure_ascii=False, indent=2))
                    return 2
                validation_run_id = service.run(
                    quant_run_id=quant_run_id, ranks=ranks, top_n=args.llm_top_n, manual=manual,
                    selected_decisions={item.strip().upper() for item in args.llm_selected_decisions.split(",") if item.strip()},
                    account_equity=Decimal(args.account_equity), available_cash=Decimal(args.available_cash),
                    continue_on_stock_error=args.continue_on_stock_error,
                    retry_failed_only=args.retry_failed_only, retry_stocks=retry_stocks,
                    reuse_successful=args.reuse_successful or args.retry_failed_only,
                )
            payload = _serialize_readback(service.readback(validation_run_id))
        finally:
            session.close()

        output_dir = Path(args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = _available_path(output_dir / _workbook_name(payload), overwrite=args.overwrite)
        temp_path = output_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp.xlsx"
        _build_excel(payload, temp_path, output_dir)
        validation = _validate_xlsx(temp_path, payload)
        os.replace(temp_path, final_path)
        workbook_hash = hashlib.sha256(final_path.read_bytes()).hexdigest()
        audit_path = final_path.with_name(final_path.stem + "_audit.json")
        sidecar = _sidecar(payload, validation, final_path, workbook_hash)
        if not _secret_scan(json.dumps(sidecar, ensure_ascii=False, default=str)):
            raise ValueError("SIDECAR_SECRET_SCAN_FAILED")
        audit_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        result = {
            "status": payload["run"]["status"], "validation_run_id": validation_run_id,
            "quant_run_id": payload["run"]["quant_run_id"], "manifest_id": payload["run"]["run_data_manifest_id"],
            "output_path": str(final_path), "audit_path": str(audit_path), "file_size": final_path.stat().st_size,
            "workbook_sha256": workbook_hash, "sheet_count": len(SHEET_NAMES),
            "generated_at": datetime.now(timezone.utc).isoformat(), "row_counts": payload["row_counts"],
            "model_preflight": preflight, "excel_validation": validation,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except FileExistsError as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, ensure_ascii=False))
        return 6
    except ValueError as exc:
        reason = str(exc)
        status = "BLOCKED" if "GATE" in reason or "INCOMPLETE" in reason else "ERROR"
        print(json.dumps({"status": status, "reason": reason}, ensure_ascii=False))
        return 2 if status == "BLOCKED" else 4
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "reason": f"{type(exc).__name__}:{exc}"}, ensure_ascii=False))
        return 6


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trader-oriented five-sheet DeepSeek screening demo")
    parser.add_argument("--quant-run", default="latest-formal")
    parser.add_argument("--validation-run", default="", help="Export an existing committed trader-demo run without new LLM calls")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--llm-ranks", nargs="+", default=None)
    group.add_argument("--llm-top-n", type=int, default=None)
    parser.add_argument("--manual-stocks", default="")
    parser.add_argument("--manual-stocks-file", default="")
    parser.add_argument("--llm-selected-decisions", default="ADVANCE")
    parser.add_argument("--knowledge-mode", default="STRUCTURED_INPUT_ONLY")
    parser.add_argument("--real-llm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-stock-error", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--retry-failed-only", action="store_true", help="Reuse successful task results and call only tasks without a prior PASS")
    parser.add_argument("--retry-stocks", default="", help="Comma-separated stock codes to rerun within the evaluation pool")
    parser.add_argument("--reuse-successful", action="store_true", help="Reuse prior successful task results, including compatible v2 results")
    parser.add_argument("--account-equity", default="1000000")
    parser.add_argument("--available-cash", default="1000000")
    parser.add_argument(
        "--output-dir",
        default=f"outputs/{date.today().isoformat()}/历史版本/交易演示",
    )
    parser.add_argument("--overwrite", type=_bool_arg, default=False)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.knowledge_mode != LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value:
        raise ValueError("HISTORICAL_RUN_CANNOT_USE_UNBOUNDED_MODEL_KNOWLEDGE")
    if args.real_llm and args.dry_run:
        raise ValueError("REAL_LLM_AND_DRY_RUN_ARE_MUTUALLY_EXCLUSIVE")
    if args.validation_run and (args.real_llm or args.dry_run):
        raise ValueError("VALIDATION_RUN_EXPORT_CANNOT_CALL_LLM")
    if Decimal(args.account_equity) <= 0 or Decimal(args.available_cash) < 0:
        raise ValueError("INVALID_VALIDATION_ACCOUNT")
    decisions = {item.strip().upper() for item in args.llm_selected_decisions.split(",") if item.strip()}
    if not decisions or not decisions.issubset({"ADVANCE", "HOLD", "REJECT", "WATCH_ONLY"}):
        raise ValueError("INVALID_LLM_SELECTED_DECISIONS")


def _parse_ranks(value: str | list[str] | None) -> tuple[int, ...] | None:
    if value is None:
        return (1, 250, 500)
    text_value = ",".join(value) if isinstance(value, list) else value
    ranks = tuple(dict.fromkeys(int(item.strip()) for item in text_value.split(",") if item.strip()))
    if not ranks or any(item <= 0 for item in ranks):
        raise ValueError("INVALID_LLM_RANKS")
    return ranks


def _parse_retry_stocks(value: str) -> set[str] | None:
    result = {normalize_ts_code(item) for item in value.split(",") if item.strip()}
    return result or None


def _manual_selections(args: argparse.Namespace) -> tuple[list[ManualSelection], str]:
    if args.manual_stocks.strip():
        rows = [ManualSelection(stock_code=item.strip()) for item in args.manual_stocks.split(",") if item.strip()]
        return _dedupe_manual(rows), "COMMAND_LINE"
    if args.manual_stocks_file.strip():
        return _dedupe_manual(_read_manual_file(Path(args.manual_stocks_file))), "FILE"
    return [], "DATABASE_MANUAL_WATCH_EMPTY"


def _read_manual_file(path: Path) -> list[ManualSelection]:
    if not path.exists():
        raise ValueError(f"MANUAL_STOCKS_FILE_NOT_FOUND:{path}")
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return _manual_from_records(list(csv.DictReader(handle)))
    if path.suffix.lower() == ".xlsx":
        return _manual_from_records(_read_first_xlsx_sheet(path))
    raise ValueError("MANUAL_STOCKS_FILE_MUST_BE_CSV_OR_XLSX")


def _read_first_xlsx_sheet(path: Path) -> list[dict[str, str]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in root.findall("m:si", ns)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rel_id = workbook.find("m:sheets/m:sheet", ns).attrib[f"{{{ns['r']}}}id"]
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_ns = {"p": "http://schemas.openxmlformats.org/package/2006/relationships"}
        target = next(node.attrib["Target"] for node in rels.findall("p:Relationship", rel_ns) if node.attrib["Id"] == rel_id)
        sheet_path = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
        root = ET.fromstring(archive.read(sheet_path))
        matrix: list[list[str]] = []
        for row in root.findall("m:sheetData/m:row", ns):
            values: list[str] = []
            for cell in row.findall("m:c", ns):
                ref = cell.attrib.get("r", "A1")
                index = _column_index(re.match(r"[A-Z]+", ref).group(0))
                while len(values) < index:
                    values.append("")
                node = cell.find("m:v", ns)
                value = "" if node is None else node.text or ""
                if cell.attrib.get("t") == "s" and value:
                    value = shared[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    inline = cell.find("m:is", ns)
                    value = "" if inline is None else "".join(inline.itertext())
                values.append(value)
            matrix.append(values)
    if not matrix:
        return []
    headers = [str(item).strip() for item in matrix[0]]
    return [{headers[index]: row[index] if index < len(row) else "" for index in range(len(headers))} for row in matrix[1:]]


def _column_index(name: str) -> int:
    result = 0
    for char in name:
        result = result * 26 + ord(char) - 64
    return result - 1


def _manual_from_records(records: list[dict[str, Any]]) -> list[ManualSelection]:
    if records and "stock_code" not in records[0]:
        raise ValueError("MANUAL_STOCKS_FILE_MISSING_STOCK_CODE")
    return [
        ManualSelection(str(row.get("stock_code") or "").strip(), str(row.get("manual_reason") or "").strip(), str(row.get("priority") or "").strip())
        for row in records if str(row.get("stock_code") or "").strip()
    ]


def _dedupe_manual(rows: list[ManualSelection]) -> list[ManualSelection]:
    result: dict[str, ManualSelection] = {}
    for row in rows:
        key = row.stock_code.strip().upper().split(".", 1)[0].zfill(6)
        result[key] = ManualSelection(key, row.reason, row.priority)
    return list(result.values())


def _serialize_readback(readback: dict[str, Any]) -> dict[str, Any]:
    run = _row_dict(readback["run"])
    samples = [_row_dict(row) for row in readback["samples"]]
    audits = [_safe_audit(_row_dict(row)) for row in readback["audits"]]
    plans = [_row_dict(row) for row in readback["plans"]]
    snapshots = [_row_dict(row) for row in readback["snapshots"]]
    allocations = [_row_dict(row) for row in readback["allocations"]]
    sample_map = {display_stock_code(item["stock_code"]): item for item in samples}
    plan_map = {display_stock_code(item["stock_code"]): item for item in plans}
    allocation_map = {display_stock_code(item["stock_code"]): item for item in allocations}
    quant_rows = []
    for row in readback["quant_rows"]:
        meta = row.factor_detail_reference or {}
        display_code = display_stock_code(row.stock_code)
        sample = sample_map.get(display_code, {})
        demo = (sample.get("screening_result") or {}).get("_trader_demo", {})
        quant_rows.append({
            "rank": row.rank, "stock_code": display_code, "stock_name": meta.get("stock_name") or display_code,
            "exchange": meta.get("exchange") or _exchange(row.stock_code), "level_one_sector": meta.get("level_one_sector") or "UNKNOWN",
            "classification_standard": meta.get("classification_standard") or "TUSHARE_STOCK_BASIC_INDUSTRY",
            "total_score": float(row.total_score), "technical_score": float(row.technical_score),
            "capital_score": float(row.capital_score), "emotion_score": float(row.emotion_score),
            "momentum_score": float(row.momentum_score), "risk_score": float(row.risk_score),
            "limit_status": meta.get("limit_status") or "UNKNOWN", "data_coverage_status": meta.get("data_coverage_status") or "UNKNOWN",
            "quant_top100": row.rank <= 100, "llm_evaluated": bool(sample), "llm_selected": bool(demo.get("llm_selected")),
            "manual_selected": bool(demo.get("manual_selected")), "selection_source": demo.get("selection_source") or "",
            "quant_run_id": run["quant_run_id"],
        })
    llm_rows = [_llm_row(sample) for sample in samples]
    candidate_samples = [sample for sample in samples if (sample.get("screening_result") or {}).get("_trader_demo", {}).get("selection_source")]
    order_rows = [_order_row(sample, plan_map.get(display_stock_code(sample["stock_code"])), allocation_map.get(display_stock_code(sample["stock_code"]))) for sample in candidate_samples]
    fundamental_rows = [_fundamental_row(sample) for sample in candidate_samples]
    errors = [
        _error_row(audit) for audit in audits
        if audit.get("schema_status") not in {"PASS", "RESOLVED_HISTORY"}
    ]
    warnings = []
    for row in order_rows:
        if row["order_status"] == "BLOCKED":
            warnings.append({"stock_code": row["stock_code"], "module": "order_plan", "status": "BLOCKED_WARNING", "reason": row["order_warning"], "affects_selection": "否", "affects_order": "是", "affects_position": "是"})
    llm_selected_count = sum(1 for row in llm_rows if row["llm_selected"])
    manual_selected_count = sum(1 for row in llm_rows if row["manual_selected"])
    flash_scores = sorted(row["llm_score"] for row in llm_rows if row["llm_score"] is not None)
    decisions = Counter(row["decision"] for row in llm_rows)
    zero_reasons = Counter(
        row["binding_constraint"] or "UNSPECIFIED"
        for row in order_rows if int(row["quantity"] or 0) == 0
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": run, "quant_rows": quant_rows, "llm_rows": llm_rows, "order_rows": order_rows,
        "fundamental_rows": fundamental_rows, "errors": errors, "warnings": warnings, "audits": audits,
        "account": snapshots[0] if snapshots else {"account_equity": 0, "available_cash": 0},
        "resolved_historical_errors": [],
        "state_summary": {
            "flash_distribution": (
                f"min={flash_scores[0]:.2f}, max={flash_scores[-1]:.2f}, unique={len(set(flash_scores))}"
                if flash_scores else "无有效Flash分数"
            ),
            "decision_distribution": "；".join(f"{key}={value}" for key, value in sorted(decisions.items())),
            "nonzero_position_count": sum(int(row["quantity"] or 0) > 0 for row in order_rows),
            "zero_position_reasons": "；".join(f"{key}={value}" for key, value in sorted(zero_reasons.items())) or "无",
        },
        "row_counts": {
            "quant_scored_count": len(quant_rows), "quant_sheet_rows": len(quant_rows),
            "llm_evaluation_count": len(llm_rows), "llm_sheet_rows": len(llm_rows),
            "llm_selected_count": llm_selected_count, "manual_selected_count": manual_selected_count,
            "trading_candidate_count": len(order_rows), "order_sheet_rows": len(order_rows),
            "fundamental_sheet_rows": len(fundamental_rows),
            "llm_advance_count": sum(1 for row in llm_rows if row["decision"] == "ADVANCE"),
            "llm_success_count": sum(1 for row in llm_rows if row["execution_status"] == "SUCCESS"),
            "llm_failure_count": sum(1 for row in llm_rows if row["execution_status"] != "SUCCESS"),
        },
    }
    scan = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if not _secret_scan(scan):
        raise ValueError("TRADER_DEMO_PAYLOAD_SECRET_SCAN_FAILED")
    payload["content_hash"] = hashlib.sha256(scan.encode("utf-8")).hexdigest()
    _validate_payload(payload)
    return payload


def _llm_row(sample: dict[str, Any]) -> dict[str, Any]:
    screening = sample.get("screening_result") or {}
    demo = screening.get("_trader_demo") or {}
    fundamental = sample.get("fundamental_result") or {}
    financial = _provenance_value(sample, "financial_status") or fundamental.get("financial_status") or {}
    errors = demo.get("errors") or []
    pro = screening.get("_pro") or {}
    return {
        "sequence": sample["rank"], "stock_code": display_stock_code(sample["stock_code"]), "stock_name": sample["stock_name"],
        "quant_rank": sample["rank"], "quant_score": _number((sample.get("quant_scores") or {}).get("total_score")),
        "llm_score": None if demo.get("execution_status") != "SUCCESS" else _number(screening.get("llm_score")),
        "decision": screening.get("screening_decision") or "分析失败",
        "confidence": _number(screening.get("confidence")), "observation_rating": fundamental.get("observation_rating") or "INSUFFICIENT_DATA",
        "financial_status": financial.get("status") if isinstance(financial, dict) else str(financial),
        "quant_consistency_score": _number(screening.get("quant_consistency_score")),
        "fundamental_quality_score": _number(screening.get("fundamental_quality_score")),
        "financial_quality_score": _number(screening.get("financial_quality_score")),
        "risk_fit_score": _number(screening.get("risk_fit_score")),
        "data_quality_score": _number(screening.get("data_quality_score")),
        "flash_score_version": screening.get("flash_score_version") or "",
        "data_conflict": bool(screening.get("data_conflict")),
        "llm_selected": bool(demo.get("llm_selected")), "manual_selected": bool(demo.get("manual_selected")),
        "trading_candidate": bool(demo.get("selection_source")), "selection_source": demo.get("selection_source") or "",
        "reason": screening.get("reason") or "分析失败",
        "risk_note": screening.get("verified_fundamental_risk_note") or screening.get("risk_note") or "",
        "fundamental_signal": screening.get("fundamental_signal") or "UNKNOWN",
        "quant_consistency_signal": screening.get("quant_consistency_signal") or "UNKNOWN",
        "financial_signal": screening.get("financial_signal") or "UNKNOWN",
        "data_quality_penalty": _number(screening.get("data_quality_penalty")) or 0,
        "risk_penalty": _number(screening.get("risk_penalty")) or 0,
        "missing_information": _readable_list(screening.get("missing_data") or sample.get("missing_fields") or []),
        "execution_status": demo.get("execution_status") or "FAILED", "error_category": _readable_list([item.get("error_category") for item in errors if item.get("error_category")]),
        "pro_score": _number(pro.get("pro_score")), "pro_rank": pro.get("pro_rank"),
        "pro_priority": pro.get("priority") or "", "pro_summary": pro.get("final_summary") or "",
    }


def _order_row(sample: dict[str, Any], plan: dict[str, Any] | None, allocation: dict[str, Any] | None) -> dict[str, Any]:
    plan = plan or {}
    allocation = allocation or {}
    screening = sample.get("screening_result") or {}
    demo = screening.get("_trader_demo") or {}
    fundamental = sample.get("fundamental_result") or {}
    pro = screening.get("_pro") or {}
    financial = _provenance_value(sample, "financial_status") or fundamental.get("financial_status") or {}
    chain = fundamental.get("industry_chain") or {}
    order_warnings = list(plan.get("warnings") or [])
    position_warnings = list(allocation.get("warnings") or [])
    return {
        "stock_code": display_stock_code(sample["stock_code"]), "stock_name": sample["stock_name"], "selection_source": demo.get("selection_source") or "",
        "manual_reason": demo.get("manual_reason") or "", "quant_rank": sample["rank"],
        "quant_score": _number((sample.get("quant_scores") or {}).get("total_score")),
        "llm_score": None if demo.get("execution_status") != "SUCCESS" else _number(screening.get("llm_score")),
        "llm_decision": screening.get("screening_decision") or "分析失败", "observation_rating": fundamental.get("observation_rating") or "INSUFFICIENT_DATA",
        "pro_score": _number(pro.get("pro_score")), "pro_rank": pro.get("pro_rank"),
        "pro_priority": pro.get("priority") or "",
        "risk_level": "HIGH" if plan.get("status") in {"BLOCKED", "NEEDS_REVIEW"} else "NORMAL",
        "conservative_price": _number(plan.get("conservative_price")), "balanced_price": _number(plan.get("balanced_price")),
        "aggressive_price": _number(plan.get("aggressive_price")), "recommended_price": _number(plan.get("recommended_price")),
        "max_acceptable_price": _number(plan.get("max_acceptable_price")), "stop_loss_price": _number(plan.get("stop_loss_price")),
        "take_profit_1": _number(plan.get("take_profit_1_price")), "take_profit_2": _number(plan.get("take_profit_2_price")),
        "risk_reward_to_tp1": _number(plan.get("risk_reward_to_tp1")),
        "risk_reward_to_tp2": _number(plan.get("risk_reward_to_tp2")),
        "active_target_mode": plan.get("active_target_mode") or "TAKE_PROFIT_2",
        "active_risk_reward": _number(plan.get("active_risk_reward") or plan.get("risk_reward")),
        "risk_reward": _number(plan.get("active_risk_reward") or plan.get("risk_reward")),
        "fill_probability": _number(plan.get("fill_probability")),
        "order_status": plan.get("status") or "BLOCKED", "order_warning": _readable_list(order_warnings),
        "relative_weight": _number(allocation.get("relative_allocation_weight")) or 0,
        "position_percent": _number(allocation.get("suggested_position_percent")) or 0,
        "capital_amount": _number(allocation.get("suggested_capital_amount")) or 0,
        "quantity": int(allocation.get("suggested_quantity") or 0), "max_loss": _number(allocation.get("estimated_max_loss")) or 0,
        "risk_budget": _number(allocation.get("estimated_max_loss")) or 0,
        "position_status": allocation.get("status") or "NON_ACTIONABLE",
        "binding_constraint": _readable_list(allocation.get("binding_constraints") or []),
        "position_warning": _readable_list(position_warnings),
        "level_one_sector": (_provenance_value(sample, "level_one_sector") or "UNKNOWN"),
        "chain_position": chain.get("chain_position") if isinstance(chain, dict) else "UNKNOWN",
        "main_business": _display_inference(fundamental.get("main_business_summary"), "summary"),
        "financial_status": financial.get("status") if isinstance(financial, dict) else str(financial),
        "actionable": "否",
    }


def _fundamental_row(sample: dict[str, Any]) -> dict[str, Any]:
    fundamental = sample.get("fundamental_result") or {}
    screening = sample.get("screening_result") or {}
    demo = screening.get("_trader_demo") or {}
    pro = screening.get("_pro") or {}
    chain = fundamental.get("industry_chain") or {}
    company = _provenance_value(sample, "company_profile") or {}
    finance = _provenance_value(sample, "financial_summary") or {}
    financial_status = _provenance_value(sample, "financial_status") or fundamental.get("financial_status") or {}
    verified_sector = _provenance_value(sample, "level_one_sector") or "UNKNOWN"
    tags = fundamental.get("concept_tags") or []
    unverified_fields = []
    for key, value in fundamental.items():
        if isinstance(value, dict) and value.get("source_status") == "LLM_UNVERIFIED":
            unverified_fields.append(key)
    return {
        "stock_code": display_stock_code(sample["stock_code"]), "stock_name": sample["stock_name"], "selection_source": demo.get("selection_source") or "",
        "quant_rank": sample["rank"],
        "llm_score": None if demo.get("execution_status") != "SUCCESS" else _number(screening.get("llm_score")),
        "industry_chain": _star(chain.get("chain_name") or "信息不足*", chain), "chain_position": _star(chain.get("chain_position") or "UNKNOWN*", chain),
        "level_one_sector": verified_sector or "UNKNOWN*", "classification_standard": "TUSHARE_STOCK_BASIC_INDUSTRY",
        "main_business": _display_inference(fundamental.get("main_business_summary"), "summary"),
        "core_products": _readable_list(fundamental.get("core_products") or company.get("main_business") or company.get("introduction") or "信息不足*"),
        "main_business_composition": _readable_list(_provenance_value(sample, "main_business") or "信息不足*"),
        "industry_position": _display_inference(fundamental.get("industry_position"), "description"),
        "concept_tags": _display_concepts(tags) or "信息不足*", "structural_theme_fit": _display_inference(fundamental.get("structural_theme_fit"), "value"),
        "competitive_advantage": _display_inference(fundamental.get("competitive_advantage"), "summary"),
        "industry_trend": _display_inference(fundamental.get("industry_trend"), "summary"),
        "investment_logic": _display_inference(fundamental.get("investment_logic"), "summary"),
        "logic_invalidation": _readable_list(fundamental.get("invalidation_conditions") or "信息不足*"), "domestic_substitution": _display_inference(fundamental.get("domestic_substitution"), "level"),
        "observation_rating": fundamental.get("observation_rating") or "INSUFFICIENT_DATA",
        "financial_status": financial_status.get("status") if isinstance(financial_status, dict) else str(financial_status),
        "financial_status_reason": _display_inference(
            fundamental.get("financial_status_explanation"), "summary"
        ) if fundamental.get("financial_status_explanation") else _readable_list(
            (financial_status.get("reason_codes") or ["信息不足*"])
            if isinstance(financial_status, dict) else ["信息不足*"]
        ),
        "financial_period": sample.get("latest_financial_period") or finance.get("end_date") or "",
        "revenue": _wan(finance.get("revenue")), "revenue_yoy": _percent(finance.get("revenue_yoy")),
        "net_profit": _wan(finance.get("net_profit")), "net_profit_yoy": _percent(finance.get("net_profit_yoy")),
        "deducted_net_profit": _wan(finance.get("deducted_net_profit")), "deducted_profit_yoy": _percent(finance.get("deducted_profit_yoy")),
        "gross_margin": _percent(finance.get("gross_margin")), "net_margin": _percent(finance.get("net_margin")),
        "operating_cash_flow": _wan(finance.get("operating_cash_flow")), "debt_ratio": _percent(finance.get("debt_ratio")),
        "cash": _wan(finance.get("cash")), "trading_financial_assets": _wan(finance.get("trading_financial_assets")),
        "total_assets": _wan(finance.get("total_assets")), "total_liabilities": _wan(finance.get("total_liabilities")),
        "unverified_fields": _readable_list(unverified_fields), "missing_fields": _readable_list(sample.get("missing_fields") or []),
        "data_conflict": "是" if screening.get("data_conflict") else "否", "fundamental_confidence": _fundamental_confidence(fundamental),
        "manual_review": "是",
        "fundamental_execution_status": fundamental.get("analysis_status") or ("FAILED" if fundamental.get("error_category") else "SUCCESS"),
        "fundamental_error_category": fundamental.get("error_category") or "",
        "input_profile_status": (demo.get("input_quality") or {}).get("status") or "UNKNOWN",
        "llm_selected": "是" if demo.get("llm_selected") else "否",
        "manual_selected": "是" if demo.get("manual_selected") else "否",
        "trading_candidate": "是" if demo.get("selection_source") else "否",
        "llm_execution_status": demo.get("execution_status") or "FAILED",
        "pro_summary": pro.get("final_summary") or "信息不足*",
        "pro_strengths": _readable_list(pro.get("key_strengths") or ["信息不足*"]),
        "pro_risks": _readable_list(pro.get("key_risks") or ["信息不足*"]),
        "pro_fundamental_quality": pro.get("fundamental_quality") or "INSUFFICIENT",
        "pro_quant_consistency": pro.get("quant_llm_consistency") or "LOW",
        "pro_manual_review_priority": pro.get("manual_review_priority") or "HIGH",
    }


def _provenance_value(sample: dict[str, Any], key: str) -> Any:
    item = (sample.get("field_provenance") or {}).get(key) or {}
    return item.get("value")


def _display_inference(value: Any, key: str) -> str:
    if isinstance(value, dict):
        return _star(value.get(key) or value.get("value") or "信息不足", value)
    return str(value or "信息不足")


def _star(value: Any, metadata: dict[str, Any]) -> str:
    text = _readable_list(value)
    return text + ("*" if metadata.get("source_status") == "LLM_UNVERIFIED" else "")


def _readable_list(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append("：".join(str(v) for v in item.values() if v not in (None, "")) or "信息不足")
            else:
                parts.append(str(item))
        return "；".join(parts)
    if isinstance(value, dict):
        return "；".join(f"{key}：{_readable_list(item)}" for key, item in value.items() if item not in (None, "", [], {}))
    return str(value)


def _display_concepts(value: Any) -> str:
    if not isinstance(value, list):
        return _readable_list(value)
    parts = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            marker = str(item.get("display_marker") or "")
            if name:
                parts.append(name + marker)
        elif str(item).strip():
            parts.append(str(item).strip())
    return "；".join(parts)


def _number(value: Any) -> float | None:
    return None if value in (None, "") else float(value)


def _percent(value: Any) -> float | None:
    return None if value in (None, "") else float(value) / 100.0


def _wan(value: Any) -> float | None:
    return None if value in (None, "") else float(value) / 10000.0


def _data_quality(sample: dict[str, Any]) -> float:
    missing = len(sample.get("missing_fields") or [])
    return max(0.0, min(1.0, 1.0 - missing * 0.1))


def _fundamental_confidence(fundamental: dict[str, Any]) -> float | None:
    values = [float(item.get("confidence")) for item in fundamental.values() if isinstance(item, dict) and item.get("confidence") is not None]
    return sum(values) / len(values) if values else None


def _error_row(audit: dict[str, Any]) -> dict[str, Any]:
    task = audit.get("task") or "llm"
    return {
        "stock_code": _audit_scope_label(audit.get("stock_code")), "module": task,
        "status": audit.get("error_category") or audit.get("schema_status") or audit.get("status"),
        "reason": f"{audit.get('error_field') or '$'}: {audit.get('error_message') or '结构化输出校验失败'}"[:240],
        "affects_selection": "是", "affects_order": "是", "affects_position": "是",
    }


def _audit_scope_label(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return display_stock_code(str(value))
    except ValueError:
        return str(value).strip().upper()[:32]


def _safe_audit(audit: dict[str, Any]) -> dict[str, Any]:
    allowed_diagnostics = {
        "brace_balance", "content_empty", "content_length", "final_error_category",
        "finish_reason", "first_non_whitespace_character", "input_stock_count",
        "json_object_detected", "last_non_whitespace_character", "local_scanner_result",
        "markdown_fence_detected", "original_failure_category", "provider_error_code",
        "provider_http_status", "provider_request_id", "repair_attempted", "repair_category",
        "repair_input_tokens", "repair_output_tokens", "response_sha256", "reused",
        "source_prompt_version", "source_validation_run_id",
        "usage_unavailable_due_pre_audit_failure", "violation_code", "violation_json_path",
        "violation_rule",
    }
    diagnostics = dict(audit.get("diagnostics") or {})
    safe = dict(audit)
    safe["diagnostics"] = {
        key: value for key, value in diagnostics.items() if key in allowed_diagnostics
    }
    if "reasoning_content_stored" in diagnostics:
        safe["diagnostics"]["reasoning_stored"] = bool(diagnostics["reasoning_content_stored"])
    return safe


def _validate_payload(payload: dict[str, Any]) -> None:
    counts = payload["row_counts"]
    if counts["quant_sheet_rows"] != counts["quant_scored_count"]:
        raise ValueError("QUANT_SHEET_ROW_COUNT_MISMATCH")
    if counts["llm_sheet_rows"] != len({row["stock_code"] for row in payload["llm_rows"]}):
        raise ValueError("LLM_SHEET_DUPLICATE_STOCK")
    order_codes = {row["stock_code"] for row in payload["order_rows"]}
    fundamental_codes = {row["stock_code"] for row in payload["fundamental_rows"]}
    expected = {row["stock_code"] for row in payload["llm_rows"] if row["llm_selected"] or row["manual_selected"]}
    evaluation_codes = {row["stock_code"] for row in payload["llm_rows"]}
    if order_codes != expected:
        raise ValueError("TRADING_CANDIDATE_SET_MISMATCH")
    if fundamental_codes != expected:
        raise ValueError("FUNDAMENTAL_CANDIDATE_POOL_MISMATCH")
    if any(row["execution_status"] != "SUCCESS" and row["llm_selected"] for row in payload["llm_rows"]):
        raise ValueError("FAILED_LLM_MARKED_SELECTED")
    if any(row["actionable"] != "否" for row in payload["order_rows"]):
        raise ValueError("ACTIONABLE_TRADER_DEMO_RESULT")


def _row_dict(row: Any) -> dict[str, Any]:
    return {column.name: _json_value(getattr(row, column.name)) for column in row.__table__.columns}


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _build_excel(payload: dict[str, Any], temp_path: Path, output_dir: Path) -> None:
    build_dir = output_dir / f".trader-demo-build-{uuid.uuid4().hex}"
    build_dir.mkdir(parents=True, exist_ok=False)
    try:
        builder = build_dir / "build_trader_demo_excel.mjs"
        shutil.copy2(ROOT_DIR / "scripts" / "build_trader_demo_excel.mjs", builder)
        shutil.copy2(ROOT_DIR / "scripts" / "excel_alignment.mjs", build_dir / "excel_alignment.mjs")
        payload_path = build_dir / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        dependency_root = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
        if not (dependency_root / "@oai/artifact-tool").exists():
            raise RuntimeError("ARTIFACT_TOOL_DEPENDENCY_NOT_FOUND")
        link = build_dir / "node_modules"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(dependency_root)], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError("ARTIFACT_TOOL_JUNCTION_FAILED")
        preview_dir = output_dir / f".{temp_path.stem}-previews"
        completed = subprocess.run(["node", str(builder), str(payload_path), str(temp_path), str(preview_dir)], cwd=build_dir, check=False)
        if completed.returncode != 0 and not temp_path.exists():
            raise RuntimeError(f"ARTIFACT_TOOL_EXPORT_FAILED:{completed.returncode}")
        _set_text_identifiers(temp_path, payload)
        _set_active_sheet(temp_path, 2)
    finally:
        link = build_dir / "node_modules"
        if link.exists():
            os.rmdir(link)
        shutil.rmtree(build_dir, ignore_errors=True)


def _validate_xlsx(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError("XLSX_EMPTY_OR_MISSING")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("XLSX_ZIP_INTEGRITY_FAILED")
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8", errors="replace")
        if len(re.findall(r"<(?:\w+:)?sheet\b", workbook_xml)) != 5 or any(name not in workbook_xml for name in SHEET_NAMES):
            raise RuntimeError("XLSX_SHEET_STRUCTURE_INVALID")
        if not re.search(r"activeTab=\"2\"", workbook_xml):
            raise RuntimeError("XLSX_DEFAULT_ACTIVE_SHEET_INVALID")
        xml = "\n".join(archive.read(name).decode("utf-8", errors="ignore") for name in archive.namelist() if name.endswith(".xml"))
        if not _secret_scan(xml):
            raise RuntimeError("XLSX_SECRET_SCAN_FAILED")
        forbidden = ("reasoning_content", "request_hash", "完整Prompt", "authorization: bearer", "{\"")
        if any(item.lower() in xml.lower() for item in forbidden):
            raise RuntimeError("XLSX_FORBIDDEN_TECHNICAL_CONTENT")
        if not ("<x:f>" in xml or "<f>" in xml):
            raise RuntimeError("XLSX_FORMULAS_MISSING")
        if "conditionalFormatting" not in xml:
            raise RuntimeError("XLSX_CONDITIONAL_FORMATTING_MISSING")
        if "tableParts" not in xml or "autoFilter" not in xml:
            raise RuntimeError("XLSX_TABLE_FILTER_MISSING")
    _validate_payload(payload)
    return {"status": "PASS", "sheet_count": 5, "zip_integrity": "PASS", "secret_scan": "PASS", **payload["row_counts"]}


def _sidecar(payload: dict[str, Any], validation: dict[str, Any], final_path: Path, workbook_hash: str) -> dict[str, Any]:
    audits = [{key: row.get(key) for key in (
        "stock_code", "task", "knowledge_mode", "model_alias", "actual_model", "prompt_version", "status",
        "schema_status", "request_hash", "input_tokens", "output_tokens", "cost_usd", "latency_ms", "cache_status",
        "error_category", "error_field", "error_message",
        "diagnostics",
    )} for row in payload["audits"]]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "workbook": final_path.name,
        "workbook_sha256": workbook_hash, "payload_content_hash": payload["content_hash"],
        "run": {key: payload["run"].get(key) for key in (
            "run_id", "quant_run_id", "run_data_manifest_id", "decision_time", "base_market_trade_date",
            "target_trade_date", "status", "knowledge_mode",
        )},
        "dataset_watermarks": payload["run"].get("expected_universe_audit") or {},
        "llm_usage": audits,
        "schema_errors": [row for row in audits if row.get("schema_status") not in {"PASS", "RESOLVED_HISTORY"}],
        "test_results": validation, "detailed_warnings": payload.get("warnings") or [],
    }


def _workbook_name(payload: dict[str, Any]) -> str:
    run_id = str(payload["run"]["quant_run_id"]).replace("quant-", "")[:8]
    base = str(payload["run"]["base_market_trade_date"]).replace("-", "")
    return f"ai_trader_demo_{base}_{run_id}.xlsx"


def _set_active_sheet(path: Path, index: int) -> None:
    patched = path.with_suffix(path.suffix + ".active.tmp")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(patched, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "xl/workbook.xml":
                text_value = content.decode("utf-8")
                if "activeTab=" in text_value:
                    text_value = re.sub(r"activeTab=\"\d+\"", f'activeTab="{index}"', text_value, count=1)
                else:
                    match = re.search(r"<([A-Za-z0-9_]+:)?workbook\b[^>]*>", text_value)
                    if not match:
                        raise RuntimeError("XLSX_WORKBOOK_ROOT_NOT_FOUND")
                    prefix = match.group(1) or ""
                    view = f'<{prefix}bookViews><{prefix}workbookView activeTab="{index}"/></{prefix}bookViews>'
                    text_value = text_value[:match.end()] + view + text_value[match.end():]
                content = text_value.encode("utf-8")
            target.writestr(info, content)
    os.replace(patched, path)


def _set_text_identifiers(path: Path, payload: dict[str, Any]) -> None:
    mappings: dict[str, dict[str, str]] = {
        "xl/worksheets/sheet1.xml": {f"B{index + 2}": row["stock_code"] for index, row in enumerate(payload["quant_rows"])},
        "xl/worksheets/sheet2.xml": {f"B{index + 2}": row["stock_code"] for index, row in enumerate(payload["llm_rows"])},
        "xl/worksheets/sheet3.xml": {f"A{index + 5}": row["stock_code"] for index, row in enumerate(payload["order_rows"])},
        "xl/worksheets/sheet4.xml": {f"A{index + 5}": row["stock_code"] for index, row in enumerate(payload["fundamental_rows"])},
    }
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ET.register_namespace("x", namespace)
    patched = path.with_suffix(path.suffix + ".text.tmp")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(patched, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            values = mappings.get(info.filename)
            if values:
                root = ET.fromstring(content)
                for cell in root.iter(f"{{{namespace}}}c"):
                    address = cell.attrib.get("r")
                    if address not in values:
                        continue
                    cell.attrib["t"] = "inlineStr"
                    for child in list(cell):
                        cell.remove(child)
                    inline = ET.SubElement(cell, f"{{{namespace}}}is")
                    text_node = ET.SubElement(inline, f"{{{namespace}}}t")
                    text_node.text = str(values[address])
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, content)
    os.replace(patched, path)


def _available_path(path: Path, *, overwrite: bool) -> Path:
    if not path.exists() or overwrite:
        return path
    stamp = datetime.now().strftime("%H%M%S")
    candidate = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
    if candidate.exists():
        raise FileExistsError(f"OUTPUT_EXISTS:{candidate}")
    return candidate


def _exchange(code: str) -> str:
    if code.startswith(("4", "8", "920")):
        return "BJ"
    return "SH" if code.startswith("6") else "SZ"


def _bool_arg(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


if __name__ == "__main__":
    raise SystemExit(main())
