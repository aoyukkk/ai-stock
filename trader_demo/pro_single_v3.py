from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, ValidationError
from sqlalchemy import inspect, select, text
from sqlalchemy.orm.attributes import flag_modified

from backend.core.config_manager import ConfigManager
from database.models.validation import ModelValidationSample, ProCandidateReview, ProResumeRun
from llm_gateway.schemas import LLMMessage, LLMRequest, LLMResponse
from llm_gateway.service import LLMGatewayService, get_llm_gateway_service
from research.output_boundary import scan_output_boundary
from stock_codes import normalize_ts_code
from trader_demo.pro_resume import (
    ProPortfolioWireV2,
    StrictWire,
    ValidationResult,
    _contains_url,
    _forbidden_business_key,
    _inference_text,
    _parse_response,
    _provenance_value,
    _pydantic_diagnostics,
    _unverified_count,
    _bounded,
    portfolio_wire_schema,
)
from trader_demo.usage_ledger import AuthoritativeUsageLedger, PIPELINE_RUN_ID, UsageContext


SINGLE_CONTRACT_VERSION = "pro_candidate_single_wire_v3"
SINGLE_PROMPT_VERSION = "pro_candidate_single_review_v3"
PORTFOLIO_CONTRACT_VERSION = "pro_portfolio_summary_wire_v3"
PORTFOLIO_PROMPT_VERSION = "pro_portfolio_summary_v3"
RANKING_VERSION = "pro_local_ranking_v3"
PREVIOUS_FAILED_RUN_ID = "pro-resume-d0871a1e7cf841a7aec4"


class ProCandidateSingleWireV3(StrictWire):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["pro_candidate_single_wire_v3"]
    stock_code: str = Field(min_length=9, max_length=12)
    pro_score: float = Field(ge=0, le=100)
    priority: Literal["HIGH", "MEDIUM", "LOW", "REVIEW_ONLY"]
    final_summary: str = Field(min_length=1, max_length=360)
    key_strengths: list[str] = Field(max_length=3)
    key_risks: list[str] = Field(max_length=3)
    fundamental_quality: Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]
    quant_llm_consistency: Literal["HIGH", "MEDIUM", "LOW", "CONFLICT"]
    manual_review_priority: Literal["HIGH", "MEDIUM", "LOW"]
    data_conflict: bool


class ProPortfolioWireV3(ProPortfolioWireV2):
    schema_version: Literal["pro_portfolio_summary_wire_v3"]


def single_wire_schema(max_item_characters: int = 160) -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "required": [
            "schema_version", "stock_code", "pro_score", "priority", "final_summary",
            "key_strengths", "key_risks", "fundamental_quality",
            "quant_llm_consistency", "manual_review_priority", "data_conflict",
        ],
        "properties": {
            "schema_version": {"type": "string", "const": SINGLE_CONTRACT_VERSION},
            "stock_code": {"type": "string"},
            "pro_score": {"type": "number", "minimum": 0, "maximum": 100},
            "priority": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "REVIEW_ONLY"]},
            "final_summary": {"type": "string", "maxLength": 360},
            "key_strengths": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": max_item_characters}},
            "key_risks": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": max_item_characters}},
            "fundamental_quality": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]},
            "quant_llm_consistency": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "CONFLICT"]},
            "manual_review_priority": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
            "data_conflict": {"type": "boolean"},
        },
    }


def portfolio_v3_schema() -> dict[str, Any]:
    schema = portfolio_wire_schema()
    schema["properties"]["schema_version"] = {
        "type": "string", "const": PORTFOLIO_CONTRACT_VERSION,
    }
    return schema


def ensure_v3_schema(engine) -> None:
    additions = {
        "pro_resume_run": {"previous_failed_run_id": "VARCHAR(64)"},
        "pro_candidate_review": {
            "contract_version": "VARCHAR(64)",
            "candidate_input_hash": "VARCHAR(64)",
            "ranking_tie_break_reason": "VARCHAR(256)",
            "ranking_version": "VARCHAR(64)",
        },
    }
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {item["name"] for item in inspect(engine).get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


@dataclass(frozen=True)
class SingleCallResult:
    value: ProCandidateSingleWireV3 | None
    report: dict[str, Any]
    input_hash: str


class ProSingleV3Failure(RuntimeError):
    def __init__(self, category: str, report: dict[str, Any]) -> None:
        self.category = category
        self.report = report
        super().__init__(category)


class ProSingleV3Service:
    def __init__(
        self,
        session,
        ledger: AuthoritativeUsageLedger,
        gateway: LLMGatewayService | None = None,
    ) -> None:
        self.session = session
        self.ledger = ledger
        self.gateway = gateway or get_llm_gateway_service()
        config = ConfigManager().get_llm_gateway_config().get("pro_resume_v3", {})
        self.config = {
            "candidate_mode": str(config.get("candidate_mode") or "single-stock"),
            "candidate_thinking": str(config.get("candidate_thinking") or "disabled"),
            "candidate_max_tokens": int(config.get("candidate_max_tokens") or 1800),
            "candidate_length_retry_max_tokens": int(config.get("candidate_length_retry_max_tokens") or 2400),
            "portfolio_thinking": str(config.get("portfolio_thinking") or "disabled"),
            "portfolio_max_tokens": int(config.get("portfolio_max_tokens") or 3200),
            "portfolio_length_retry_max_tokens": int(config.get("portfolio_length_retry_max_tokens") or 4800),
            "concurrency": int(config.get("concurrency") or 3),
            "max_item_characters": int(config.get("max_item_characters") or 160),
        }
        if self.config["candidate_thinking"] != "disabled" or self.config["portfolio_thinking"] != "disabled":
            raise ValueError("PRO_V3_THINKING_MUST_BE_DISABLED")

    def run_canary(
        self,
        resume: ProResumeRun,
        canary: list[ModelValidationSample],
        sources: dict[str, str],
        checkpoint_path: Path,
    ) -> list[dict[str, Any]]:
        reports = self.run_reviews(
            resume, canary, sources, checkpoint_path,
            stage="CANARY", stop_on_failure=True,
        )
        if len(reports) != 3 or any(report["schema_status"] != "PASS" for report in reports):
            raise ProSingleV3Failure("PRO_V3_CANARY_FAILED", reports[-1] if reports else {})
        return reports

    def run_reviews(
        self,
        resume: ProResumeRun,
        candidates: list[ModelValidationSample],
        sources: dict[str, str],
        checkpoint_path: Path,
        *,
        stage: str,
        stop_on_failure: bool,
    ) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        pending = []
        for sample in candidates:
            code = normalize_ts_code(sample.stock_code)
            compact, _ = _single_input(sample, sources)
            input_hash = hashlib.sha256(
                json.dumps(compact, ensure_ascii=False, sort_keys=True, default=str).encode()
            ).hexdigest()
            existing = self.session.scalar(select(ProCandidateReview).where(
                ProCandidateReview.pro_resume_run_id == resume.run_id,
                ProCandidateReview.stock_code == code,
                ProCandidateReview.review_status == "SUCCESS",
            ))
            if existing is not None:
                if (
                    existing.contract_version != SINGLE_CONTRACT_VERSION
                    or existing.prompt_version != SINGLE_PROMPT_VERSION
                    or existing.candidate_input_hash != input_hash
                ):
                    raise ValueError(f"PRO_V3_CANDIDATE_INPUT_HASH_MISMATCH:{code}")
                reports.append(_reused_report(code, stage))
            else:
                pending.append(sample)
        for offset in range(0, len(pending), self.config["concurrency"]):
            batch = pending[offset:offset + self.config["concurrency"]]
            results: dict[str, SingleCallResult] = {}
            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                futures = {
                    executor.submit(self._review_one, resume, sample, sources, stage): normalize_ts_code(sample.stock_code)
                    for sample in batch
                }
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
            failed: SingleCallResult | None = None
            for sample in batch:
                code = normalize_ts_code(sample.stock_code)
                result = results[code]
                reports.append(result.report)
                if result.value is None:
                    failed = result
                    continue
                self._persist_review(resume, result.value, result.report, result.input_hash)
                _write_v3_checkpoint(
                    checkpoint_path, resume, reports,
                    completed_codes=self._successful_codes(resume.run_id),
                )
            if failed is not None and stop_on_failure:
                resume.status = "PARTIAL_PRO_FAILURE"
                resume.config_snapshot = {**dict(resume.config_snapshot or {}), f"{stage.lower()}_reports": reports}
                self.session.commit()
                raise ProSingleV3Failure(f"PRO_V3_{stage}_FAILED", failed.report)
        resume.config_snapshot = {**dict(resume.config_snapshot or {}), f"{stage.lower()}_reports": reports}
        self.session.commit()
        return reports

    def rank_and_apply(
        self,
        resume: ProResumeRun,
        candidates: list[ModelValidationSample],
        sources: dict[str, str],
    ) -> list[ProCandidateReview]:
        reviews = list(self.session.scalars(select(ProCandidateReview).where(
            ProCandidateReview.pro_resume_run_id == resume.run_id,
            ProCandidateReview.review_status == "SUCCESS",
        )))
        expected_count = len(candidates)
        if len(reviews) != expected_count or len({row.stock_code for row in reviews}) != expected_count:
            raise ValueError("PRO_V3_RESULT_SET_INCOMPLETE")
        by_code = {normalize_ts_code(sample.stock_code): sample for sample in candidates}
        reviews = deterministic_v3_review_order(reviews, by_code)
        before = _source_snapshot(candidates, sources)
        for rank, review in enumerate(reviews, start=1):
            review.pro_rank = rank
            review.ranking_version = RANKING_VERSION
            review.ranking_tie_break_reason = "pro_score>priority>manual_review_priority>flash_score>quant_rank>stock_code"
            sample = by_code[review.stock_code]
            screening = json.loads(json.dumps(sample.screening_result or {}, default=str))
            screening["_pro"] = {
                "stock_code": review.stock_code, "pro_score": float(review.pro_score),
                "pro_rank": rank, "priority": review.priority,
                "final_summary": review.final_summary, "key_strengths": review.key_strengths,
                "key_risks": review.key_risks, "fundamental_quality": review.fundamental_quality,
                "quant_llm_consistency": review.quant_llm_consistency,
                "manual_review_priority": review.manual_review_priority,
                "data_conflict": review.data_conflict, "prompt_version": review.prompt_version,
                "actual_model": review.actual_model, "review_status": review.review_status,
                "ranking_version": RANKING_VERSION, "source_status": "LLM_UNVERIFIED",
            }
            sample.screening_result = screening
            flag_modified(sample, "screening_result")
        self.session.commit()
        if [row.pro_rank for row in reviews] != list(range(1, expected_count + 1)):
            raise ValueError("PRO_V3_RANK_SEQUENCE_INVALID")
        if _source_snapshot(candidates, sources) != before:
            raise ValueError("PRO_V3_MUTATED_SOURCE_FACTS")
        return reviews

    def run_portfolio(
        self,
        resume: ProResumeRun,
        candidates: list[ModelValidationSample],
        reviews: list[ProCandidateReview],
        sources: dict[str, str],
    ) -> tuple[ProPortfolioWireV3, dict[str, Any]]:
        payload = _portfolio_input(candidates, reviews, sources)
        expected_codes = {normalize_ts_code(sample.stock_code) for sample in candidates}
        response = self.gateway.chat(self._portfolio_request(payload, retry=False))
        call_id = f"{resume.run_id}:portfolio:initial"
        self._record(response, resume, call_id, "pro_portfolio_v3", PORTFOLIO_PROMPT_VERSION)
        checked = _validate_portfolio(response, expected_codes)
        self.ledger.update_outcome(call_id, status="SUCCESS" if checked.value else "FAILED",
                                   error_category=str(checked.diagnostics.get("error_category") or ""),
                                   diagnostics=checked.diagnostics)
        responses = [response]
        if checked.value is None:
            category = str(checked.diagnostics.get("error_category") or "")
            repaired = self.gateway.chat(self._portfolio_repair_request(
                response.content, checked.diagnostics, sorted(expected_codes),
                max_tokens=self.config["portfolio_length_retry_max_tokens"] if category == "JSON_TRUNCATED" else self.config["portfolio_max_tokens"],
            ))
            repair_id = f"{resume.run_id}:portfolio:repair"
            self._record(repaired, resume, repair_id, "pro_portfolio_v3_repair", PORTFOLIO_PROMPT_VERSION)
            checked = _validate_portfolio(repaired, expected_codes)
            self.ledger.update_outcome(repair_id, status="SUCCESS" if checked.value else "FAILED",
                                       error_category=str(checked.diagnostics.get("error_category") or ""),
                                       diagnostics=checked.diagnostics)
            responses.append(repaired)
        report = _responses_report("portfolio", responses, checked)
        if checked.value is None:
            resume.status = "PARTIAL_PRO_FAILURE"
            self.session.commit()
            raise ProSingleV3Failure("PRO_V3_PORTFOLIO_FAILED", report)
        portfolio = checked.value
        assert isinstance(portfolio, ProPortfolioWireV3)
        resume.portfolio_result = portfolio.model_dump(mode="json")
        resume.status = "PRO_SUCCESS"
        for sample in candidates:
            screening = json.loads(json.dumps(sample.screening_result or {}, default=str))
            screening.setdefault("_pro", {})["portfolio_summary"] = resume.portfolio_result
            sample.screening_result = screening
            flag_modified(sample, "screening_result")
        self.session.commit()
        return portfolio, report

    def _review_one(
        self,
        resume: ProResumeRun,
        sample: ModelValidationSample,
        sources: dict[str, str],
        stage: str,
    ) -> SingleCallResult:
        self._assert_budget(resume.run_id)
        compact, compaction = _single_input(sample, sources)
        input_hash = hashlib.sha256(json.dumps(compact, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        code = normalize_ts_code(sample.stock_code)
        response = self.gateway.chat(self._candidate_request(compact, retry=False, max_tokens=self.config["candidate_max_tokens"]))
        call_id = f"{resume.run_id}:{code}:initial"
        self._record(response, resume, call_id, "pro_candidate_single_review", SINGLE_PROMPT_VERSION,
                     extra={"stock_code": code, "candidate_input_hash": input_hash, **compaction, "stage": stage})
        checked = _validate_single(response, code, self.config["max_item_characters"])
        self.ledger.update_outcome(call_id, status="SUCCESS" if checked.value else "FAILED",
                                   error_category=str(checked.diagnostics.get("error_category") or ""),
                                   diagnostics=checked.diagnostics)
        responses = [response]
        if checked.value is None:
            category = str(checked.diagnostics.get("error_category") or "")
            retry_tokens = self.config["candidate_length_retry_max_tokens"] if category == "JSON_TRUNCATED" else self.config["candidate_max_tokens"]
            if category in {"EMPTY_JSON_CONTENT", "JSON_TRUNCATED"}:
                request = self._candidate_request(compact, retry=True, max_tokens=retry_tokens)
            else:
                request = self._candidate_repair_request(code, response.content, checked.diagnostics, retry_tokens)
            repaired = self.gateway.chat(request)
            repair_id = f"{resume.run_id}:{code}:repair"
            self._record(repaired, resume, repair_id, "pro_candidate_single_review_repair", SINGLE_PROMPT_VERSION,
                         extra={"stock_code": code, "candidate_input_hash": input_hash, "stage": stage})
            checked = _validate_single(repaired, code, self.config["max_item_characters"])
            self.ledger.update_outcome(repair_id, status="SUCCESS" if checked.value else "FAILED",
                                       error_category=str(checked.diagnostics.get("error_category") or ""),
                                       diagnostics=checked.diagnostics)
            responses.append(repaired)
        report = _responses_report(code, responses, checked)
        report.update({**compaction, "candidate_input_hash": input_hash, "stage": stage})
        return SingleCallResult(checked.value, report, input_hash)

    def _persist_review(
        self,
        resume: ProResumeRun,
        value: ProCandidateSingleWireV3,
        report: dict[str, Any],
        input_hash: str,
    ) -> None:
        code = normalize_ts_code(value.stock_code)
        existing = self.session.scalar(select(ProCandidateReview).where(
            ProCandidateReview.pro_resume_run_id == resume.run_id,
            ProCandidateReview.stock_code == code,
        ))
        if existing is not None:
            raise ValueError(f"PRO_V3_SUCCESS_RESULT_ALREADY_EXISTS:{code}")
        self.session.add(ProCandidateReview(
            pro_resume_run_id=resume.run_id, flash_validation_run_id=resume.flash_validation_run_id,
            chunk_id="single", contract_version=SINGLE_CONTRACT_VERSION,
            candidate_input_hash=input_hash, stock_code=code,
            pro_score=Decimal(str(value.pro_score)), pro_rank=None,
            ranking_tie_break_reason=None, ranking_version=None,
            priority=value.priority, final_summary=value.final_summary,
            key_strengths=value.key_strengths, key_risks=value.key_risks,
            fundamental_quality=value.fundamental_quality,
            quant_llm_consistency=value.quant_llm_consistency,
            manual_review_priority=value.manual_review_priority,
            data_conflict=value.data_conflict, prompt_version=SINGLE_PROMPT_VERSION,
            actual_model=report["actual_model"], review_status="SUCCESS",
        ))
        self.session.commit()

    def _record(
        self, response: LLMResponse, resume: ProResumeRun, call_id: str,
        task: str, prompt_version: str, extra: dict[str, Any] | None = None,
    ) -> None:
        self.ledger.record_response(response, UsageContext(
            call_id=call_id, pipeline_run_id=PIPELINE_RUN_ID,
            validation_run_id=resume.flash_validation_run_id,
            pro_resume_run_id=resume.run_id, agent_name="controller_agent",
            task=task, task_type="committee_controller", task_tier="HIGH_IMPACT",
            prompt_version=prompt_version,
        ))
        if extra:
            self.ledger.update_outcome(call_id, status=response.status, diagnostics=extra)

    def _assert_budget(self, resume_run_id: str) -> None:
        ledger = self.ledger.summary(pro_resume_run_id=resume_run_id)
        if int(ledger["pro_candidate_actual"]) >= 300_000:
            raise ValueError("PRO_V3_CANDIDATE_BUDGET_REACHED")
        if int(ledger["pro_candidate_repair_actual"]) >= 100_000:
            raise ValueError("PRO_V3_CANDIDATE_REPAIR_BUDGET_REACHED")
        if int(ledger["total_actual_api_tokens"]) >= 4_700_000:
            raise ValueError("TOTAL_TOKEN_SAFETY_RESERVE_REACHED")

    def _successful_codes(self, run_id: str) -> list[str]:
        return sorted(self.session.scalars(select(ProCandidateReview.stock_code).where(
            ProCandidateReview.pro_resume_run_id == run_id,
            ProCandidateReview.review_status == "SUCCESS",
        )).all())

    def _candidate_request(self, compact: dict[str, Any], *, retry: bool, max_tokens: int) -> LLMRequest:
        return LLMRequest(
            agent_name="controller_agent", task="pro_candidate_single_review",
            task_type="committee_controller", task_tier="HIGH_IMPACT",
            model_alias="controller-high-capability", temperature=0.1,
            messages=[
                LLMMessage(role="system", content=(
                    "Review exactly one supplied stock using only supplied facts. Return one JSON object matching wire_schema. "
                    "Do not output rank, prices, positions, trading instructions, URLs, news, customers, orders, market shares, or reasoning."
                )),
                LLMMessage(role="user", content=json.dumps({
                    "wire_schema": single_wire_schema(self.config["max_item_characters"]),
                    "candidate": compact, "retry_after_empty_or_length": retry,
                }, ensure_ascii=False, separators=(",", ":"), default=str)),
            ],
            max_tokens=max_tokens, prompt_version=SINGLE_PROMPT_VERSION,
            json_mode=False, response_schema=None, allow_fallback=False,
            thinking_mode="disabled", reasoning_effort=None,
            metadata={"structured": True, "search_provider_enabled": False, "pro_contract": SINGLE_CONTRACT_VERSION},
        )

    def _candidate_repair_request(
        self, code: str, candidate: str, diagnostics: dict[str, Any], max_tokens: int,
    ) -> LLMRequest:
        example = {
            "schema_version": SINGLE_CONTRACT_VERSION, "stock_code": code,
            "pro_score": 0, "priority": "REVIEW_ONLY", "final_summary": "信息不足，需人工复核。",
            "key_strengths": [], "key_risks": [], "fundamental_quality": "INSUFFICIENT",
            "quant_llm_consistency": "LOW", "manual_review_priority": "HIGH", "data_conflict": False,
        }
        return LLMRequest(
            agent_name="controller_agent", task="pro_candidate_single_review_repair",
            task_type="committee_controller", task_tier="HIGH_IMPACT",
            model_alias="controller-high-capability", temperature=0.1,
            messages=[
                LLMMessage(role="system", content="Repair one JSON object only. Do not add commentary or reasoning."),
                LLMMessage(role="user", content=json.dumps({
                    "stock_code": code, "error_category": diagnostics.get("error_category"),
                    "error_paths": diagnostics.get("schema_error_paths") or [],
                    "wire_example": example, "invalid_candidate": (candidate or "")[:8000],
                }, ensure_ascii=False, separators=(",", ":"))),
            ],
            max_tokens=max_tokens, prompt_version=SINGLE_PROMPT_VERSION,
            json_mode=False, response_schema=None, allow_fallback=False,
            thinking_mode="disabled", reasoning_effort=None,
            metadata={"structured": True, "search_provider_enabled": False, "repair": True},
        )

    def _portfolio_request(self, payload: dict[str, Any], *, retry: bool) -> LLMRequest:
        return LLMRequest(
            agent_name="controller_agent", task="pro_portfolio_summary_v3",
            task_type="committee_controller", task_tier="HIGH_IMPACT",
            model_alias="controller-high-capability", temperature=0.1,
            messages=[
                LLMMessage(role="system", content=(
                    "Summarize only the ranked candidate identifiers and precomputed distributions. Return the shallow JSON wire. "
                    "Do not output prices, positions, instructions, URLs, news, or reasoning."
                )),
                LLMMessage(role="user", content=json.dumps({
                    "wire_schema": portfolio_v3_schema(), **payload, "retry": retry,
                }, ensure_ascii=False, separators=(",", ":"), default=str)),
            ],
            max_tokens=self.config["portfolio_max_tokens"], prompt_version=PORTFOLIO_PROMPT_VERSION,
            json_mode=False, response_schema=None, allow_fallback=False,
            thinking_mode="disabled", reasoning_effort=None,
            metadata={"structured": True, "search_provider_enabled": False, "pro_contract": PORTFOLIO_CONTRACT_VERSION},
        )

    def _portfolio_repair_request(
        self, candidate: str, diagnostics: dict[str, Any], codes: list[str], *, max_tokens: int,
    ) -> LLMRequest:
        return LLMRequest(
            agent_name="controller_agent", task="pro_portfolio_summary_v3_repair",
            task_type="committee_controller", task_tier="HIGH_IMPACT",
            model_alias="controller-high-capability", temperature=0.1,
            messages=[
                LLMMessage(role="system", content="Repair the portfolio JSON only. Do not add commentary or reasoning."),
                LLMMessage(role="user", content=json.dumps({
                    "allowed_stock_codes": codes,
                    "error_category": diagnostics.get("error_category"),
                    "error_paths": diagnostics.get("schema_error_paths") or [],
                    "wire_schema": portfolio_v3_schema(),
                    "invalid_candidate": (candidate or "")[:12000],
                }, ensure_ascii=False, separators=(",", ":"))),
            ],
            max_tokens=max_tokens, prompt_version=PORTFOLIO_PROMPT_VERSION,
            json_mode=False, response_schema=None, allow_fallback=False,
            thinking_mode="disabled", reasoning_effort=None,
            metadata={"structured": True, "search_provider_enabled": False, "repair": True},
        )


def stable_v3_order(samples: list[ModelValidationSample]) -> list[ModelValidationSample]:
    source_order = {"BOTH": 0, "MANUAL": 1, "LLM_TOP20": 2}
    return sorted(samples, key=lambda sample: (
        source_order[str((sample.screening_result or {}).get("_trader_demo", {}).get("selection_source"))],
        -float((sample.screening_result or {}).get("llm_score") or -1),
        int(sample.rank), normalize_ts_code(sample.stock_code),
    ))


def deterministic_v3_review_order(reviews, by_code):
    priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "REVIEW_ONLY": 3}
    manual_priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(reviews, key=lambda row: (
        -float(row.pro_score), priority[row.priority], manual_priority[row.manual_review_priority],
        -float((by_code[row.stock_code].screening_result or {}).get("llm_score") or 0),
        int(by_code[row.stock_code].rank), row.stock_code,
    ))


def select_v3_canary(samples: list[ModelValidationSample]) -> list[ModelValidationSample]:
    by_code = {normalize_ts_code(sample.stock_code): sample for sample in samples}
    first = by_code["603019.SH"]
    second = max(
        (sample for sample in samples if normalize_ts_code(sample.stock_code) != "603019.SH"),
        key=lambda sample: (float((sample.screening_result or {}).get("llm_score") or -1), -int(sample.rank)),
    )
    manual = [sample for sample in samples if (sample.screening_result or {}).get("_trader_demo", {}).get("manual_selected")]
    third = max(manual, key=lambda sample: int(sample.rank))
    selected = [first, second, third]
    if len({normalize_ts_code(sample.stock_code) for sample in selected}) != 3:
        raise ValueError("PRO_V3_CANARY_NOT_DISTINCT")
    return selected


def _single_input(sample: ModelValidationSample, sources: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    code = normalize_ts_code(sample.stock_code)
    fundamental = sample.fundamental_result or {}
    screening = sample.screening_result or {}
    quant = sample.quant_scores or {}
    provenance = sample.field_provenance or {}
    financial = fundamental.get("financial_status") or {}
    chain = fundamental.get("industry_chain") or {}
    products = fundamental.get("core_products") or []
    payload = {
        "stock_code": code, "stock_name": _bounded(sample.stock_name, 40),
        "selection_source": sources[code],
        "manual_reason": _bounded((screening.get("_trader_demo") or {}).get("manual_reason") or "", 120),
        "quant_rank": sample.rank, "quant_score": quant.get("total_score"),
        "technical_score": quant.get("technical_score"), "capital_score": quant.get("capital_score"),
        "emotion_score": quant.get("emotion_score"), "momentum_score": quant.get("momentum_score"),
        "risk_score": quant.get("risk_score"), "flash_score": screening.get("llm_score"),
        "flash_decision": screening.get("screening_decision"), "flash_confidence": screening.get("confidence"),
        "financial_status": financial.get("status") if isinstance(financial, dict) else str(financial),
        "observation_rating": fundamental.get("observation_rating") or "INSUFFICIENT_DATA",
        "structured_industry": _bounded(_provenance_value(provenance, "level_one_sector") or "UNKNOWN", 80),
        "industry_chain_summary": _bounded(chain.get("chain_name") or "UNKNOWN", 100),
        "main_business_summary": _bounded(_inference_text(fundamental.get("main_business_summary")), 220),
        "core_products_summary": [_bounded(item, 80) for item in products[:6]],
        "competitive_advantage_summary": _bounded(_inference_text(fundamental.get("competitive_advantage")), 180),
        "investment_logic_summary": _bounded(_inference_text(fundamental.get("investment_logic")), 220),
        "domestic_substitution": _bounded(_inference_text(fundamental.get("domestic_substitution")), 100),
        "data_quality_score": screening.get("data_quality_score") or 0,
        "unverified_field_count": _unverified_count(fundamental),
        "missing_field_count": len(sample.missing_fields or []),
        "hard_risk_status": financial.get("status") if isinstance(financial, dict) else "UNKNOWN",
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return payload, {
        "input_character_count": len(serialized),
        "estimated_input_tokens": max(1, len(serialized) // 4),
        "omitted_fields": ["full_fundamental_profile", "financial_statements", "raw_provider_json", "full_flash_output", "factor_detail"],
        "compaction_version": "pro_single_input_compaction_v3",
    }


def _validate_single(response: LLMResponse, expected_code: str, max_item_characters: int) -> ValidationResult:
    parsed, diagnostics = _parse_response_with_safe_tail(response)
    if response.model != "deepseek-v4-pro" or response.thinking_mode != "disabled":
        return ValidationResult(None, {**diagnostics, "error_category": "PRO_V3_RESOLVED_MODEL_CONFIG_MISMATCH"})
    if parsed is not None and response.finish_reason != "stop":
        return ValidationResult(None, {**diagnostics, "error_category": "PRO_V3_UNEXPECTED_FINISH_REASON"})
    if parsed is None:
        return ValidationResult(None, diagnostics)
    if _forbidden_business_key(parsed):
        return ValidationResult(None, {**diagnostics, "error_category": "FORBIDDEN_PRICE_OR_POSITION_FIELD", "schema_error_paths": ["$"]})
    try:
        value = ProCandidateSingleWireV3.model_validate(parsed)
    except ValidationError as exc:
        diagnostics.update(_pydantic_diagnostics(exc))
        return ValidationResult(None, diagnostics)
    try:
        code = normalize_ts_code(value.stock_code)
    except ValueError:
        return ValidationResult(None, {**diagnostics, "error_category": "STOCK_CODE_INVALID", "schema_error_paths": ["$.stock_code"]})
    if code != expected_code:
        return ValidationResult(None, {**diagnostics, "error_category": "STOCK_CODE_MISMATCH", "schema_error_paths": ["$.stock_code"]})
    if any(len(item) > max_item_characters for item in value.key_strengths + value.key_risks):
        return ValidationResult(None, {**diagnostics, "error_category": "TEXT_LENGTH_EXCEEDED"})
    violation = scan_output_boundary(value.model_dump(mode="json"), expected_stock_code=expected_code)
    if violation:
        return ValidationResult(None, {**diagnostics, "error_category": violation.category, "schema_error_paths": [violation.path]})
    diagnostics.update({"schema_status": "PASS", "error_category": "", "resolved_thinking_mode": response.thinking_mode})
    return ValidationResult(value.model_copy(update={"stock_code": code}), diagnostics)


def _validate_portfolio(response: LLMResponse, expected_codes: set[str]) -> ValidationResult:
    parsed, diagnostics = _parse_response_with_safe_tail(response)
    if response.model != "deepseek-v4-pro" or response.thinking_mode != "disabled":
        return ValidationResult(None, {**diagnostics, "error_category": "PRO_V3_RESOLVED_MODEL_CONFIG_MISMATCH"})
    if parsed is not None and response.finish_reason != "stop":
        return ValidationResult(None, {**diagnostics, "error_category": "PRO_V3_UNEXPECTED_FINISH_REASON"})
    if parsed is None:
        return ValidationResult(None, diagnostics)
    if _forbidden_business_key(parsed):
        return ValidationResult(None, {**diagnostics, "error_category": "FORBIDDEN_PRICE_OR_POSITION_FIELD"})
    try:
        value = ProPortfolioWireV3.model_validate(parsed)
    except ValidationError as exc:
        diagnostics.update(_pydantic_diagnostics(exc))
        return ValidationResult(None, diagnostics)
    try:
        codes = [normalize_ts_code(code) for code in value.top_priority_stock_codes]
    except ValueError:
        return ValidationResult(None, {**diagnostics, "error_category": "PORTFOLIO_STOCK_CODE_INVALID"})
    if len(codes) != len(set(codes)) or not set(codes).issubset(expected_codes):
        return ValidationResult(None, {**diagnostics, "error_category": "PORTFOLIO_EXTRA_STOCK"})
    if _contains_url(value.model_dump(mode="json")):
        return ValidationResult(None, {**diagnostics, "error_category": "FABRICATED_URL"})
    diagnostics.update({"schema_status": "PASS", "error_category": "", "resolved_thinking_mode": response.thinking_mode})
    return ValidationResult(value.model_copy(update={"top_priority_stock_codes": codes}), diagnostics)


def _parse_response_with_safe_tail(response: LLMResponse) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    parsed, diagnostics = _parse_response(response)
    if parsed is not None or diagnostics.get("error_category") != "INVALID_JSON":
        return parsed, diagnostics
    text_value = (response.content or "").strip()
    if text_value.startswith("```"):
        lines = text_value.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text_value = "\n".join(lines).strip()
    start = text_value.find("{")
    if start < 0:
        return None, diagnostics
    try:
        value, end = json.JSONDecoder().raw_decode(text_value[start:])
    except json.JSONDecodeError:
        return None, diagnostics
    tail = text_value[start + end:].strip()
    if "{" in tail or "}" in tail or not isinstance(value, dict):
        return None, {**diagnostics, "error_category": "MULTIPLE_OR_AMBIGUOUS_JSON"}
    diagnostics.update({"json_parse_status": "PASS_WITH_SAFE_TRAILING_TEXT_REMOVAL", "trailing_text_removed": bool(tail)})
    return value, diagnostics


def _portfolio_input(candidates, reviews, sources) -> dict[str, Any]:
    by_code = {normalize_ts_code(sample.stock_code): sample for sample in candidates}
    rows = []
    sectors: dict[str, int] = {}
    chains: dict[str, int] = {}
    risks: dict[str, int] = {}
    financials: dict[str, int] = {}
    quality: dict[str, int] = {}
    zero_warning = 0
    for review in sorted(reviews, key=lambda row: int(row.pro_rank or 999)):
        sample = by_code[review.stock_code]
        fundamental = sample.fundamental_result or {}
        provenance = sample.field_provenance or {}
        sector = str(_provenance_value(provenance, "level_one_sector") or "UNKNOWN")
        chain = str((fundamental.get("industry_chain") or {}).get("chain_name") or "UNKNOWN")
        financial = str((fundamental.get("financial_status") or {}).get("status") or "UNKNOWN")
        risk = financial
        for mapping, key in ((sectors, sector), (chains, chain), (risks, risk), (financials, financial), (quality, review.fundamental_quality)):
            mapping[key] = mapping.get(key, 0) + 1
        if risk in {"BLOCK", "BLOCKED", "HIGH", "HIGH_RISK", "BLACK_SWAN"}:
            zero_warning += 1
        rows.append({
            "stock_code": review.stock_code, "stock_name": sample.stock_name,
            "selection_source": sources[review.stock_code], "pro_rank": review.pro_rank,
            "pro_score": float(review.pro_score), "priority": review.priority,
            "industry": sector, "industry_chain": chain, "hard_risk_status": risk,
        })
    return {
        "candidates": rows,
        "computed_statistics": {
            "sector_distribution": sectors, "industry_chain_distribution": chains,
            "manual_top20_overlap_count": sum(value == "BOTH" for value in sources.values()),
            "risk_distribution": risks, "financial_status_distribution": financials,
            "data_quality_distribution": quality, "zero_position_warning_count": zero_warning,
        },
    }


def _responses_report(identifier: str, responses: list[LLMResponse], checked: ValidationResult) -> dict[str, Any]:
    return {
        "stock_code": identifier, "actual_model": responses[-1].model,
        "resolved_thinking_mode": responses[-1].thinking_mode,
        "schema_status": "PASS" if checked.value else "FAILED",
        "repair_attempted": len(responses) > 1,
        "input_tokens": sum(response.input_tokens for response in responses),
        "output_tokens": sum(response.output_tokens for response in responses),
        "latency_ms": sum(response.latency_ms for response in responses),
        "cost_usd": round(sum(float(response.cost_usd or 0) for response in responses), 8),
        "finish_reason": responses[-1].finish_reason,
        "diagnostics": checked.diagnostics, "usage_persisted": True,
    }


def _reused_report(code: str, stage: str) -> dict[str, Any]:
    return {
        "stock_code": code, "stage": stage, "actual_model": "deepseek-v4-pro",
        "resolved_thinking_mode": "disabled", "schema_status": "PASS",
        "repair_attempted": False, "input_tokens": 0, "output_tokens": 0,
        "latency_ms": 0, "cost_usd": 0, "usage_persisted": True,
        "usage_source": "RESUME_REUSED",
    }


def _write_v3_checkpoint(path: Path, resume: ProResumeRun, reports: list[dict[str, Any]], completed_codes: list[str]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stage"] = "PRO_SINGLE_V3"
    payload["pro_v3"] = {
        "pro_resume_run_id": resume.run_id, "contract_version": SINGLE_CONTRACT_VERSION,
        "candidate_prompt_version": SINGLE_PROMPT_VERSION,
        "portfolio_prompt_version": PORTFOLIO_PROMPT_VERSION,
        "candidate_set_hash": resume.candidate_set_hash,
        "completed_codes": completed_codes, "reports": reports,
        "updated_at": time.time(), "quant_rerun": False, "flash_rerun": False,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _source_snapshot(candidates, sources) -> dict[str, Any]:
    return {
        normalize_ts_code(sample.stock_code): {
            "quant": dict(sample.quant_scores or {}),
            "flash_score": (sample.screening_result or {}).get("llm_score"),
            "flash_decision": (sample.screening_result or {}).get("screening_decision"),
            "financial_status": (sample.fundamental_result or {}).get("financial_status"),
            "selection_source": sources[normalize_ts_code(sample.stock_code)],
        }
        for sample in candidates
    }
