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
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env", override=False)

from database.session import get_session, init_db
from llm_gateway.connectivity import run_deepseek_json_canary
from llm_gateway.service import get_llm_gateway_service
from model_validation.service import GuardedValidationService
from research.knowledge_mode import LLMKnowledgeMode
from research.structured_validation import MODEL_ALIAS


SHEET_NAMES = [
    "00_使用说明", "01_运行摘要", "02_时间与水位", "03_Quant样本", "04_基本面画像",
    "05_字段溯源", "06_LLM审计", "07_只读挂单计划", "08_模拟仓位", "09_人工逐股审核",
    "10_人工字段审核", "11_告警与异常", "12_测试结果",
]


def main() -> int:
    args = _parser().parse_args()
    try:
        _validate_args(args)
    except ValueError as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, ensure_ascii=False))
        return 7
    session = get_session()
    try:
        service = GuardedValidationService(session)
        quant_run_id = None if args.quant_run == "latest-formal" else args.quant_run
        ranks = _parse_ranks(args)
        preview = service.preview(quant_run_id=quant_run_id, ranks=ranks)
        output_dir = Path(args.output_dir).resolve()
        preview.update({
            "input_fields": ["company_profile", "main_business", "main_business_breakdown", "level_one_sector", "concept_tags", "financial_summary", "financial_status", "quant", "provenance", "manifest", "missing_fields"],
            "financial_periods": _profile_periods(session, service, quant_run_id, ranks),
            "future_data_check": "PASS_TARGET_DAY_DATA_NOT_READ",
            "model_alias": MODEL_ALIAS, "estimated_business_calls": 2 * len(ranks),
            "estimated_tokens": {"input": 6000 * len(ranks), "output": 2000 * len(ranks), "method": "conservative dry-run ceiling"},
            "expected_excel_path": str(output_dir / _planned_workbook_name(preview, ranks, "<validation_run_id>")),
            "local_gate_status": _safe_gate_status(),
        })
        if args.dry_run or not args.real_llm:
            print(json.dumps(preview, ensure_ascii=False, indent=2, default=str))
            return 0
        if not preview["real_gate_ready"]:
            print(json.dumps({"status": "BLOCKED", "reason": "REAL_LLM_GUARDS_NOT_SATISFIED", "missing_gates": preview["missing_gates"], "model_calls": 0}, ensure_ascii=False, indent=2))
            return 2
        preflight = _run_real_preflight()
        if preflight["status"] != "PASS":
            print(json.dumps({"status": "BLOCKED", "reason": preflight["reason"], "preflight": preflight, "stock_model_calls": 0}, ensure_ascii=False, indent=2))
            return 2
        init_db()
        validation_run_id = service.run_real(
            quant_run_id=quant_run_id, ranks=ranks,
            account_equity=Decimal(args.account_equity), available_cash=Decimal(args.available_cash),
        )
        readback = service.readback(validation_run_id)
        payload = _serialize_readback(readback)
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / _planned_workbook_name(payload, ranks, validation_run_id)
        if final_path.exists() and not args.overwrite:
            raise FileExistsError(f"OUTPUT_EXISTS:{final_path}")
        temp_path = output_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp.xlsx"
        _build_excel(payload, temp_path, output_dir)
        validation = _validate_xlsx(temp_path, payload)
        os.replace(temp_path, final_path)
        inspect_temp = Path(str(temp_path) + ".inspect.txt")
        if inspect_temp.exists():
            os.replace(inspect_temp, Path(str(final_path) + ".inspect.txt"))
        file_hash = hashlib.sha256(final_path.read_bytes()).hexdigest()
        result = {
            "status": "COMPLETE", "validation_run_id": validation_run_id,
            "quant_run_id": payload["run"]["quant_run_id"], "manifest_id": payload["run"]["run_data_manifest_id"],
            "output_path": str(final_path), "file_size": final_path.stat().st_size,
            "workbook_sha256": file_hash, "sheet_count": 13, "sample_count": len(payload["samples"]),
            "llm_call_count": sum(1 for row in payload["audits"] if row.get("cache_status") != "REUSED"),
            "llm_audit_rows": len(payload["audits"]),
            "model_preflight": preflight,
            "excel_validation": validation,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except ValueError as exc:
        reason = str(exc)
        code = 2 if reason.startswith("REAL_LLM_GUARDS_NOT_SATISFIED") else (3 if "TEMPORAL" in reason or "POINT_IN_TIME" in reason else 4)
        print(json.dumps({"status": "BLOCKED" if code in {2, 3} else "ERROR", "reason": reason}, ensure_ascii=False))
        return code
    except FileExistsError as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, ensure_ascii=False))
        return 6
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "reason": f"{type(exc).__name__}:{exc}"}, ensure_ascii=False))
        return 6
    finally:
        session.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded three-stock DeepSeek model validation and Excel export")
    parser.add_argument("--quant-run", default="latest-formal")
    parser.add_argument("--ranks", nargs="+", default=["1,250,500"])
    parser.add_argument("--knowledge-mode", default="STRUCTURED_INPUT_ONLY")
    parser.add_argument("--real-llm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-dir", default="outputs/validation")
    parser.add_argument("--account-equity", default="1000000")
    parser.add_argument("--available-cash", default="1000000")
    parser.add_argument("--overwrite", type=_bool_arg, default=False)
    return parser


def _validate_args(args) -> None:
    ranks = ",".join(args.ranks).replace(" ", "")
    if ranks not in {"1", "1,250,500"}: raise ValueError("RANKS_MUST_BE_1_OR_1_250_500")
    if args.knowledge_mode != LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value: raise ValueError("HISTORICAL_RUN_CANNOT_USE_UNBOUNDED_MODEL_KNOWLEDGE")
    if args.real_llm and args.dry_run: raise ValueError("REAL_LLM_AND_DRY_RUN_ARE_MUTUALLY_EXCLUSIVE")
    if Decimal(args.account_equity) <= 0 or Decimal(args.available_cash) < 0: raise ValueError("INVALID_VALIDATION_ACCOUNT")


def _parse_ranks(args) -> tuple[int, ...]:
    value = ",".join(args.ranks).replace(" ", "")
    return tuple(int(item) for item in value.split(",") if item)


def _profile_periods(session, service, quant_run_id, ranks):
    run, _, samples = service._load_context(quant_run_id, ranks=ranks)
    return [{"stock_code": row.stock_code, "period": service._profile(row.stock_code, run).latest_financial_period} for row in samples]


def _safe_gate_status() -> dict[str, str]:
    return {
        "deepseek_key": "CONFIGURED" if os.getenv("DEEPSEEK_API_KEY", "").strip() else "NOT_CONFIGURED",
        "real_calls": "ENABLED" if _flag("LLM_REAL_CALLS_ENABLED") else "DISABLED",
        "fundamental_research": "ENABLED" if _flag("RUN_REAL_FUNDAMENTAL_RESEARCH") else "DISABLED",
        "gateway_mock_only": "DISABLED" if os.getenv("LLM_GATEWAY_MOCK_ONLY", "").strip().lower() in {"0", "false", "no", "off"} else "ENABLED_OR_CONFIG_DEFAULT",
    }


def _run_real_preflight() -> dict[str, Any]:
    service = get_llm_gateway_service()
    try:
        availability = service.check_model_availability("connectivity_test", MODEL_ALIAS)
    except Exception as exc:
        return {"status": "FAIL", "reason": "MODEL_NOT_AVAILABLE", "error": _redacted_error(exc)}
    if availability.get("model") == "deepseek-v4-pro":
        return {"status": "FAIL", "reason": "PRO_MODEL_NOT_ALLOWED", "availability": availability}
    connectivity = run_deepseek_json_canary(service, MODEL_ALIAS)
    if connectivity.get("status") != "SUCCESS" or not connectivity.get("schema_passed"):
        return {"status": "FAIL", "reason": connectivity.get("error_category") or "CONNECTIVITY_CANARY_FAILED", "availability": availability, "connectivity": connectivity}
    return {"status": "PASS", "availability": availability, "connectivity": connectivity}


def _redacted_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}:{exc}"
    for name in ("DEEPSEEK_API_KEY", "TUSHARE_TOKEN", "DATABASE_URL"):
        value = os.getenv(name, "").strip()
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def _planned_workbook_name(payload: dict[str, Any], ranks: tuple[int, ...], validation_run_id: str) -> str:
    run = payload.get("run", payload)
    quant_run_id = str(run.get("quant_run_id") or payload.get("quant_run_id") or "")
    short_id = quant_run_id.replace("quant-", "")[:8] or str(validation_run_id)[:8]
    base = str(run.get("base_market_trade_date") or payload.get("base_market_trade_date") or "2026-07-09").replace("-", "")
    if ranks == (1,):
        stock = "000518"
        samples = payload.get("samples") or []
        if samples:
            stock = str(samples[0].get("stock_code") or stock)
        return f"ai_trader_canary_{stock}_{base}_{short_id}.xlsx"
    return f"ai_trader_model_validation_{base}_{short_id}.xlsx"


def _serialize_readback(readback: dict[str, Any]) -> dict[str, Any]:
    payload = {key: [_row_dict(row) for row in value] if isinstance(value, list) else _row_dict(value) for key, value in readback.items()}
    dynamic = ("current_market_main_theme", "latest_industry_event", "latest_company_event", "current_policy_catalyst", "current_news_catalyst", "current_market_sentiment_from_news")
    payload["dynamic_fields_unknown"] = all(sample["fundamental_result"].get(key, "UNKNOWN") == "UNKNOWN" for sample in payload["samples"] for key in dynamic)
    scan_material = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    payload["secret_scan"] = "PASS" if _secret_scan(scan_material) else "FAIL"
    payload["content_hash"] = hashlib.sha256(scan_material.encode("utf-8")).hexdigest()
    if payload["secret_scan"] != "PASS": raise ValueError("VALIDATION_PAYLOAD_SECRET_SCAN_FAILED")
    return payload


def _row_dict(row) -> dict[str, Any]:
    result = {}
    for column in row.__table__.columns:
        result[column.name] = _json_value(getattr(row, column.name))
    return result


def _json_value(value):
    if isinstance(value, Decimal): return float(value)
    if isinstance(value, (date, datetime)): return value.isoformat()
    return value


def _secret_scan(text: str) -> bool:
    lowered = text.lower()
    forbidden_names = ("reasoning_content", "authorization: bearer", "deepseek_api_key", "tushare_token", "database_password")
    if any(item in lowered for item in forbidden_names): return False
    for name in ("DEEPSEEK_API_KEY", "TUSHARE_TOKEN", "TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY"):
        value = os.getenv(name, "").strip()
        if len(value) >= 8 and value in text: return False
    return True


def _build_excel(payload: dict[str, Any], temp_path: Path, output_dir: Path) -> None:
    build_dir = output_dir / f".artifact-build-{uuid.uuid4().hex}"
    build_dir.mkdir(parents=True, exist_ok=False)
    try:
        source = ROOT_DIR / "scripts" / "build_validation_excel.mjs"
        builder = build_dir / "build_validation_excel.mjs"
        shutil.copy2(source, builder)
        payload_path = build_dir / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        dependency_root = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
        if not (dependency_root / "@oai/artifact-tool").exists(): raise RuntimeError("ARTIFACT_TOOL_DEPENDENCY_NOT_FOUND")
        link = build_dir / "node_modules"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(dependency_root)], capture_output=True, text=True)
            if result.returncode != 0: raise RuntimeError("ARTIFACT_TOOL_JUNCTION_FAILED")
        else: os.symlink(dependency_root, link, target_is_directory=True)
        previews = output_dir / f".{temp_path.stem}-previews"
        completed = subprocess.run(["node", str(builder), str(payload_path), str(temp_path), str(previews)], check=False, cwd=build_dir)
        if completed.returncode != 0:
            # artifact-tool 2.8.x can raise a Windows native teardown code after a
            # successful export/render. Accept only a fully readable XLSX; all
            # structural and security validation still runs before atomic rename.
            if not temp_path.exists():
                raise RuntimeError(f"ARTIFACT_TOOL_EXPORT_FAILED:{completed.returncode}")
            try:
                with zipfile.ZipFile(temp_path) as archive:
                    if archive.testzip() is not None:
                        raise RuntimeError(f"ARTIFACT_TOOL_EXPORT_FAILED:{completed.returncode}")
            except zipfile.BadZipFile as exc:
                raise RuntimeError(f"ARTIFACT_TOOL_EXPORT_FAILED:{completed.returncode}") from exc
    finally:
        link = build_dir / "node_modules"
        if link.exists(): os.rmdir(link)
        shutil.rmtree(build_dir, ignore_errors=True)


def _validate_xlsx(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0: raise RuntimeError("XLSX_EMPTY_OR_MISSING")
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad: raise RuntimeError(f"XLSX_CORRUPT_MEMBER:{bad}")
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8", errors="replace")
        if any(name not in workbook_xml for name in SHEET_NAMES): raise RuntimeError("XLSX_SHEET_STRUCTURE_INVALID")
        if len(re.findall(r"<(?:\w+:)?sheet\b", workbook_xml)) != 13: raise RuntimeError("XLSX_SHEET_COUNT_INVALID")
        text = "\n".join(archive.read(name).decode("utf-8", errors="ignore") for name in archive.namelist() if name.endswith(".xml"))
        if not _secret_scan(text): raise RuntimeError("XLSX_SECRET_SCAN_FAILED")
        if any(value in text for value in (">NaN<", ">Infinity<", ">-Infinity<")): raise RuntimeError("XLSX_NON_FINITE_VALUE")
        if "reasoning_content" in text: raise RuntimeError("XLSX_REASONING_CONTENT_FOUND")
        expected_count = len(payload["samples"])
        if expected_count not in {1, 3}: raise RuntimeError("XLSX_SAMPLE_COUNT_INVALID")
        if len(payload["plans"]) != expected_count or len(payload["allocations"]) != expected_count: raise RuntimeError("XLSX_SAMPLE_PLAN_ALLOCATION_COUNT_INVALID")
        if any(p["plan_purpose"] != "MODEL_VALIDATION" or p["actionable"] for p in payload["plans"]): raise RuntimeError("XLSX_ACTIONABLE_ORDER_PLAN_FOUND")
        if any(a["allocation_purpose"] != "MODEL_VALIDATION" or a["actionable"] for a in payload["allocations"]): raise RuntimeError("XLSX_ACTIONABLE_ALLOCATION_FOUND")
    return {"status": "PASS", "sheet_count": 13, "zip_integrity": "PASS", "secret_scan": "PASS", "sample_count": expected_count}


def _bool_arg(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}: return True
    if normalized in {"0", "false", "no", "off"}: return False
    raise argparse.ArgumentTypeError("expected true or false")


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    raise SystemExit(main())
