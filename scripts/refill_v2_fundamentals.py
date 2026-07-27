from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.config import get_app_config
from database.models.quant_run import QuantRun
from database.models.temporal import RunDataManifestRecord
from database.session import get_session, init_db
from fundamentals.pipeline import CachedTushareProfileService
from llm_gateway.schemas import LLMMessage, LLMRequest
from llm_gateway.service import get_llm_gateway_service
from scripts.build_v2_corrected_human_daily_output import (
    DEFAULT_SOURCE_ROOT,
    _code,
    _manual_rows,
    _read_json,
)
from trader_demo.runtime import temporary_real_llm_runtime


class WorkbookFundamentalInference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str
    industry_chain: str
    chain_position: Literal["上游", "中游", "下游", "服务平台", "多环节", "信息不足"]
    main_business: str
    core_products: list[str] = Field(default_factory=list, max_length=8)
    concept_tags: list[str] = Field(default_factory=list, max_length=8)
    structural_direction: str
    competitive_advantage: str
    industry_trend: str
    investment_logic: str
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=8)
    financial_status: str
    financial_summary: str
    evidence_limitations: list[str] = Field(default_factory=list, max_length=8)


WORKBOOK_SYSTEM_PROMPT = """你负责补全A股人工复核工作簿的基本面摘要。
只能使用输入中的Tushare结构化公司资料、主营业务、产品、行业和财务状态进行归纳。
不得声称联网搜索，不得编造URL、客户、订单、市场份额、排名、公告编号或输入中没有的精确数字。
内容不足时可以明确写“信息不足”，但不得因为一个次要字段缺失而把整行全部写成信息不足。
financial_status和financial_summary必须遵守输入中的financial_status规则结果，不得自行升级。
不输出买卖建议、价格、仓位或最终评分。只输出符合JSON Schema的JSON。"""


def _infer_workbook_fields(
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    gateway = get_llm_gateway_service()
    response = gateway.chat(
        LLMRequest(
            agent_name="fundamental_research",
            task="workbook_fundamental_refill",
            task_type="fundamental_inference",
            model_alias="search-analysis-fast",
            messages=[
                LLMMessage(role="system", content=WORKBOOK_SYSTEM_PROMPT),
                LLMMessage(
                    role="user",
                    content=json.dumps(context, ensure_ascii=False, default=str)[:16000],
                ),
            ],
            prompt_version="workbook_fundamental_refill_v1",
            response_schema=WorkbookFundamentalInference.model_json_schema(),
            json_mode=True,
            thinking_mode="disabled",
            max_tokens=1800,
            allow_fallback=False,
            metadata={
                "stock_code": context.get("stock_code"),
                "research_mode": "WORKBOOK_REFILL_UNVERIFIED",
                "input_profile_version": context.get("profile_version"),
            },
        )
    )
    if response.status != "ok":
        raise RuntimeError(
            f"LLM_GATEWAY_{response.status.upper()}:{str(response.error or '')[:300]}"
        )
    payload = response.structured_output or response.parsed_json or {}
    result = WorkbookFundamentalInference.model_validate(payload)
    usage = {
        "model_alias": response.model_alias,
        "actual_model": response.model,
        "request_hash": response.request_hash,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "latency_ms": response.latency_ms,
        "cost_usd": response.cost_usd,
        "prompt_version": "workbook_fundamental_refill_v1",
        "generated_at": datetime.now().isoformat(),
        "input_profile_version": context.get("profile_version"),
        "cached": response.cached,
    }
    return result.model_dump(mode="json"), usage


def _candidate_rows(trade_date: date) -> list[dict[str, str]]:
    audit = _read_json(
        DEFAULT_SOURCE_ROOT / trade_date.isoformat() / "monday_v2_candidate_audit.json"
    )
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in sorted(
        (audit.get("pro") or {}).get("results") or [],
        key=lambda item: int(item.get("pro_rank") or 999),
    ):
        code = _code(row.get("stock_code"))
        if code and code not in seen:
            rows.append(
                {
                    "stock_code": code,
                    "stock_name": str(row.get("stock_name") or code),
                    "selection_source": "MODEL_TOP20",
                }
            )
            seen.add(code)
    for row in _manual_rows(trade_date):
        code = _code(row.get("stock_code"))
        if code and code not in seen:
            rows.append(
                {
                    "stock_code": code,
                    "stock_name": code,
                    "selection_source": "MANUAL",
                }
            )
            seen.add(code)
    return rows


def _temporal_context(
    session, trade_date: date
) -> tuple[QuantRun, RunDataManifestRecord]:
    runs = list(
        session.scalars(
            select(QuantRun)
            .where(
                QuantRun.base_market_trade_date == trade_date,
                QuantRun.actionable.is_(True),
            )
            .order_by(QuantRun.created_at.desc())
        )
    )
    for run in runs:
        manifest = session.scalar(
            select(RunDataManifestRecord).where(
                RunDataManifestRecord.manifest_id == run.data_manifest_id,
                RunDataManifestRecord.actionable.is_(True),
            )
        )
        if manifest and manifest.temporal_status in {"PASS", "PASS_WITH_WARNINGS"}:
            return run, manifest
    raise RuntimeError("ACTIONABLE_TEMPORAL_CONTEXT_NOT_FOUND")


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temp, path)


def run(trade_date: date, *, output: Path) -> dict[str, Any]:
    get_app_config()
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        raise RuntimeError("DEEPSEEK_API_KEY_NOT_CONFIGURED")
    init_db()
    session = get_session()
    try:
        quant_run, manifest = _temporal_context(session, trade_date)
        candidates = _candidate_rows(trade_date)
        checkpoint: dict[str, Any] = {
            "phase": "V2_FUNDAMENTAL_LLM_REFILL",
            "trade_date": trade_date.isoformat(),
            "created_at": datetime.now().isoformat(),
            "quant_run_id": quant_run.run_id,
            "data_manifest_id": manifest.manifest_id,
            "temporal_status": manifest.temporal_status,
            "candidate_count": len(candidates),
            "rows": [],
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
            "status": "RUNNING",
        }
        _write_checkpoint(output, checkpoint)
        profile_service = CachedTushareProfileService()
        with temporary_real_llm_runtime():
            for index, candidate in enumerate(candidates, 1):
                code = candidate["stock_code"]
                row: dict[str, Any] = {
                    **candidate,
                    "sequence": index,
                    "status": "RUNNING",
                }
                try:
                    profile = profile_service.build(code)
                    inference, usage = _infer_workbook_fields(
                        profile.model_dump(mode="json")
                    )
                    row.update(
                        {
                            "status": "COMPLETE",
                            "profile": profile.model_dump(mode="json"),
                            "inference": inference,
                            "usage": usage,
                        }
                    )
                except Exception as exc:
                    row.update(
                        {
                            "status": "FAILED",
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:500],
                        }
                    )
                checkpoint["rows"].append(row)
                checkpoint["completed_count"] = sum(
                    item["status"] == "COMPLETE" for item in checkpoint["rows"]
                )
                checkpoint["failed_count"] = sum(
                    item["status"] == "FAILED" for item in checkpoint["rows"]
                )
                checkpoint["updated_at"] = datetime.now().isoformat()
                _write_checkpoint(output, checkpoint)
        checkpoint["status"] = (
            "COMPLETE"
            if checkpoint.get("failed_count", 0) == 0
            else "COMPLETE_WITH_FAILURES"
        )
        checkpoint["actual_network_calls"] = sum(
            1
            for item in checkpoint["rows"]
            if item.get("status") == "COMPLETE"
            and not (item.get("usage") or {}).get("cached", False)
        )
        checkpoint["input_tokens"] = sum(
            int((item.get("usage") or {}).get("input_tokens") or 0)
            for item in checkpoint["rows"]
        )
        checkpoint["output_tokens"] = sum(
            int((item.get("usage") or {}).get("output_tokens") or 0)
            for item in checkpoint["rows"]
        )
        checkpoint["cost_usd"] = round(
            sum(
                float((item.get("usage") or {}).get("cost_usd") or 0.0)
                for item in checkpoint["rows"]
            ),
            6,
        )
        checkpoint["finished_at"] = datetime.now().isoformat()
        _write_checkpoint(output, checkpoint)
        return checkpoint
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade-date", default="2026-07-24")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.trade_date)
    output = args.output or (
        ROOT
        / "outputs"
        / trade_date.isoformat()
        / "审计"
        / f"基本面LLM补全_{datetime.now():%Y%m%dT%H%M%S}.json"
    )
    result = run(trade_date, output=output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(output.resolve()),
                "candidate_count": result["candidate_count"],
                "completed_count": result.get("completed_count", 0),
                "failed_count": result.get("failed_count", 0),
                "actual_network_calls": result.get("actual_network_calls", 0),
                "input_tokens": result.get("input_tokens", 0),
                "output_tokens": result.get("output_tokens", 0),
                "cost_usd": result.get("cost_usd", 0),
                "orders": 0,
                "scheduler": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
