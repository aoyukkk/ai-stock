from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from database.models.validation import ProCandidateReview, ProResumeRun, ModelValidationSample
from llm_gateway.schemas import LLMMessage, LLMRequest, LLMResponse
from llm_gateway.service import LLMGatewayService, get_llm_gateway_service
from research.output_boundary import scan_output_boundary
from stock_codes import normalize_ts_code
from trader_demo.usage_ledger import AuthoritativeUsageLedger, PIPELINE_RUN_ID, UsageContext


PRO_CONTRACT_VERSION = "pro_candidate_wire_v2"
PRO_CANDIDATE_PROMPT_VERSION = "pro_candidate_review_v2"
PRO_PORTFOLIO_PROMPT_VERSION = "pro_portfolio_summary_v2"
PRO_MODEL_ALIAS = "controller-high-capability"


class StrictWire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProCandidateResultV2(StrictWire):
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


class ProCandidateWireV2(StrictWire):
    schema_version: Literal["pro_candidate_wire_v2"]
    chunk_id: str = Field(min_length=1, max_length=64)
    results: list[ProCandidateResultV2] = Field(min_length=1, max_length=5)


class ProPortfolioWireV2(StrictWire):
    schema_version: Literal["pro_portfolio_wire_v2"]
    overall_summary: str = Field(min_length=1, max_length=600)
    portfolio_risk_level: Literal["LOW", "MEDIUM", "HIGH", "REVIEW_REQUIRED"]
    sector_concentration_notes: list[str] = Field(max_length=6)
    industry_chain_concentration_notes: list[str] = Field(max_length=6)
    manual_pool_notes: list[str] = Field(max_length=6)
    top_priority_stock_codes: list[str] = Field(max_length=10)
    key_portfolio_risks: list[str] = Field(max_length=8)
    manual_review_focus: list[str] = Field(max_length=8)
    data_conflict: bool


def candidate_wire_schema() -> dict[str, Any]:
    result = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "stock_code", "pro_score", "priority", "final_summary", "key_strengths",
            "key_risks", "fundamental_quality", "quant_llm_consistency",
            "manual_review_priority", "data_conflict",
        ],
        "properties": {
            "stock_code": {"type": "string"},
            "pro_score": {"type": "number", "minimum": 0, "maximum": 100},
            "priority": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "REVIEW_ONLY"]},
            "final_summary": {"type": "string", "maxLength": 360},
            "key_strengths": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 160}},
            "key_risks": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 160}},
            "fundamental_quality": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]},
            "quant_llm_consistency": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "CONFLICT"]},
            "manual_review_priority": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
            "data_conflict": {"type": "boolean"},
        },
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "chunk_id", "results"],
        "properties": {
            "schema_version": {"type": "string", "const": PRO_CONTRACT_VERSION},
            "chunk_id": {"type": "string"},
            "results": {"type": "array", "minItems": 1, "maxItems": 5, "items": result},
        },
    }


def portfolio_wire_schema() -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "required": [
            "schema_version", "overall_summary", "portfolio_risk_level",
            "sector_concentration_notes", "industry_chain_concentration_notes",
            "manual_pool_notes", "top_priority_stock_codes", "key_portfolio_risks",
            "manual_review_focus", "data_conflict",
        ],
        "properties": {
            "schema_version": {"type": "string", "const": "pro_portfolio_wire_v2"},
            "overall_summary": {"type": "string", "maxLength": 600},
            "portfolio_risk_level": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "REVIEW_REQUIRED"]},
            "sector_concentration_notes": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 180}},
            "industry_chain_concentration_notes": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 180}},
            "manual_pool_notes": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 180}},
            "top_priority_stock_codes": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
            "key_portfolio_risks": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 180}},
            "manual_review_focus": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 180}},
            "data_conflict": {"type": "boolean"},
        },
    }


@dataclass(frozen=True)
class ValidationResult:
    value: BaseModel | None
    diagnostics: dict[str, Any]


class ProStageFailure(RuntimeError):
    def __init__(self, category: str, stage_report: dict[str, Any]) -> None:
        self.category = category
        self.stage_report = stage_report
        super().__init__(category)


class ProResumeService:
    def __init__(
        self,
        session,
        ledger: AuthoritativeUsageLedger,
        gateway: LLMGatewayService | None = None,
    ) -> None:
        self.session = session
        self.ledger = ledger
        self.gateway = gateway or get_llm_gateway_service()

    def run_candidate_chunks(
        self,
        resume: ProResumeRun,
        candidates: list[ModelValidationSample],
        *,
        selection_sources: dict[str, str],
        chunks: list[list[ModelValidationSample]],
        model_alias: str = PRO_MODEL_ALIAS,
    ) -> list[dict[str, Any]]:
        prior_reports = list((resume.config_snapshot or {}).get("chunk_reports") or [])
        failed_prior = next((item for item in prior_reports if item.get("schema_status") == "FAILED"), None)
        if resume.status == "PARTIAL_PRO_FAILURE" and failed_prior:
            raise ProStageFailure("FAILED_CHUNK_REQUIRES_NEW_PRO_RESUME_RUN", failed_prior)
        reports: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_id = f"candidate-{index:02d}"
            expected_codes = [normalize_ts_code(sample.stock_code) for sample in chunk]
            existing = list(self.session.scalars(
                select(ProCandidateReview).where(
                    ProCandidateReview.pro_resume_run_id == resume.run_id,
                    ProCandidateReview.chunk_id == chunk_id,
                    ProCandidateReview.review_status == "SUCCESS",
                )
            ))
            if {row.stock_code for row in existing} == set(expected_codes):
                reports.append({
                    "chunk_id": chunk_id, "stock_count": len(chunk), "stock_codes": expected_codes,
                    "actual_model": existing[0].actual_model, "schema_status": "PASS",
                    "repair_attempted": False, "input_tokens": 0, "output_tokens": 0,
                    "latency_ms": 0, "cost_usd": 0, "usage_source": "RESUME_REUSED",
                })
                continue
            compact = [self._compact_candidate(sample, selection_sources) for sample in chunk]
            result, report = self._candidate_call(
                resume, chunk_id, compact, expected_codes, model_alias=model_alias
            )
            reports.append(report)
            if result is None:
                resume.status = "PARTIAL_PRO_FAILURE"
                resume.config_snapshot = {**dict(resume.config_snapshot or {}), "chunk_reports": reports}
                self.session.commit()
                raise ProStageFailure("PARTIAL_PRO_FAILURE", report)
            for item in result.results:
                self.session.add(ProCandidateReview(
                    pro_resume_run_id=resume.run_id,
                    flash_validation_run_id=resume.flash_validation_run_id,
                    chunk_id=chunk_id, stock_code=normalize_ts_code(item.stock_code),
                    pro_score=Decimal(str(item.pro_score)), pro_rank=None,
                    priority=item.priority, final_summary=item.final_summary,
                    key_strengths=item.key_strengths, key_risks=item.key_risks,
                    fundamental_quality=item.fundamental_quality,
                    quant_llm_consistency=item.quant_llm_consistency,
                    manual_review_priority=item.manual_review_priority,
                    data_conflict=item.data_conflict,
                    prompt_version=PRO_CANDIDATE_PROMPT_VERSION,
                    actual_model=report["actual_model"], review_status="SUCCESS",
                ))
            self.session.commit()
        resume.config_snapshot = {**dict(resume.config_snapshot or {}), "chunk_reports": reports}
        self.session.commit()
        return reports

    def rank_locally_and_apply(
        self,
        resume: ProResumeRun,
        candidates: list[ModelValidationSample],
        selection_sources: dict[str, str],
    ) -> list[ProCandidateReview]:
        reviews = list(self.session.scalars(
            select(ProCandidateReview).where(ProCandidateReview.pro_resume_run_id == resume.run_id)
        ))
        if len(reviews) != len(candidates) or len({row.stock_code for row in reviews}) != len(candidates):
            raise ValueError("PRO_CANDIDATE_RESULT_SET_INCOMPLETE")
        by_code = {normalize_ts_code(sample.stock_code): sample for sample in candidates}
        reviews = deterministic_review_order(reviews, by_code)
        score_snapshot = {
            code: (
                dict(sample.quant_scores or {}),
                (sample.screening_result or {}).get("llm_score"),
                (sample.screening_result or {}).get("screening_decision"),
                (sample.fundamental_result or {}).get("financial_status"),
                selection_sources[code],
            )
            for code, sample in by_code.items()
        }
        for rank, review in enumerate(reviews, start=1):
            review.pro_rank = rank
            sample = by_code[review.stock_code]
            screening = json.loads(json.dumps(sample.screening_result or {}, default=str))
            screening["_pro"] = {
                "stock_code": review.stock_code, "pro_score": float(review.pro_score),
                "pro_rank": rank, "priority": review.priority,
                "final_summary": review.final_summary,
                "key_strengths": review.key_strengths, "key_risks": review.key_risks,
                "fundamental_quality": review.fundamental_quality,
                "quant_llm_consistency": review.quant_llm_consistency,
                "manual_review_priority": review.manual_review_priority,
                "data_conflict": review.data_conflict, "chunk_id": review.chunk_id,
                "prompt_version": review.prompt_version, "actual_model": review.actual_model,
                "review_status": review.review_status, "source_status": "LLM_UNVERIFIED",
            }
            sample.screening_result = screening
            flag_modified(sample, "screening_result")
        self.session.commit()
        after = {
            code: (
                dict(sample.quant_scores or {}),
                (sample.screening_result or {}).get("llm_score"),
                (sample.screening_result or {}).get("screening_decision"),
                (sample.fundamental_result or {}).get("financial_status"),
                selection_sources[code],
            )
            for code, sample in by_code.items()
        }
        if after != score_snapshot:
            raise ValueError("PRO_MUTATED_SOURCE_FACTS")
        return reviews

    def run_portfolio_summary(
        self,
        resume: ProResumeRun,
        candidates: list[ModelValidationSample],
        reviews: list[ProCandidateReview],
        *,
        selection_sources: dict[str, str],
        model_alias: str = PRO_MODEL_ALIAS,
    ) -> tuple[ProPortfolioWireV2, dict[str, Any]]:
        payload = self._portfolio_payload(candidates, reviews, selection_sources)
        expected_codes = {normalize_ts_code(sample.stock_code) for sample in candidates}
        value, report = self._portfolio_call(resume, payload, expected_codes, model_alias=model_alias)
        if value is None:
            resume.status = "PARTIAL_PRO_FAILURE"
            resume.config_snapshot = {**dict(resume.config_snapshot or {}), "portfolio_report": report}
            self.session.commit()
            raise ProStageFailure("PRO_PORTFOLIO_SUMMARY_FAILED", report)
        resume.portfolio_result = value.model_dump(mode="json")
        resume.status = "PRO_SUCCESS"
        resume.config_snapshot = {**dict(resume.config_snapshot or {}), "portfolio_report": report}
        for sample in candidates:
            screening = json.loads(json.dumps(sample.screening_result or {}, default=str))
            screening.setdefault("_pro", {})["portfolio_summary"] = value.model_dump(mode="json")
            sample.screening_result = screening
            flag_modified(sample, "screening_result")
        self.session.commit()
        return value, report

    def _candidate_call(
        self,
        resume: ProResumeRun,
        chunk_id: str,
        compact: list[dict[str, Any]],
        expected_codes: list[str],
        *,
        model_alias: str,
    ) -> tuple[ProCandidateWireV2 | None, dict[str, Any]]:
        first_call_id = f"{resume.run_id}:{chunk_id}:initial"
        response = self.gateway.chat(self._candidate_request(chunk_id, compact, model_alias))
        self._record(response, resume, first_call_id, f"pro_candidate_chunk:{chunk_id}", PRO_CANDIDATE_PROMPT_VERSION)
        checked = self._validate_candidate(response, chunk_id, expected_codes)
        self.ledger.update_outcome(
            first_call_id, status="SUCCESS" if checked.value else "FAILED",
            error_category=str(checked.diagnostics.get("error_category") or ""),
            diagnostics=checked.diagnostics,
        )
        responses = [response]
        repair_attempted = False
        if checked.value is None:
            repair_attempted = True
            repair_call_id = f"{resume.run_id}:{chunk_id}:repair"
            repaired = self.gateway.chat(self._candidate_repair_request(
                chunk_id, expected_codes, response.content, checked.diagnostics, model_alias
            ))
            self._record(
                repaired, resume, repair_call_id, f"pro_candidate_chunk:{chunk_id}_repair",
                PRO_CANDIDATE_PROMPT_VERSION,
            )
            checked = self._validate_candidate(repaired, chunk_id, expected_codes)
            self.ledger.update_outcome(
                repair_call_id, status="SUCCESS" if checked.value else "FAILED",
                error_category=str(checked.diagnostics.get("error_category") or ""),
                diagnostics=checked.diagnostics,
            )
            responses.append(repaired)
        return checked.value, _stage_report(chunk_id, expected_codes, responses, checked, repair_attempted)

    def _portfolio_call(
        self,
        resume: ProResumeRun,
        payload: dict[str, Any],
        expected_codes: set[str],
        *,
        model_alias: str,
    ) -> tuple[ProPortfolioWireV2 | None, dict[str, Any]]:
        first_call_id = f"{resume.run_id}:portfolio:initial"
        response = self.gateway.chat(self._portfolio_request(payload, model_alias))
        self._record(response, resume, first_call_id, "pro_portfolio_summary", PRO_PORTFOLIO_PROMPT_VERSION)
        checked = self._validate_portfolio(response, expected_codes)
        self.ledger.update_outcome(
            first_call_id, status="SUCCESS" if checked.value else "FAILED",
            error_category=str(checked.diagnostics.get("error_category") or ""), diagnostics=checked.diagnostics,
        )
        responses = [response]
        repair_attempted = False
        if checked.value is None:
            repair_attempted = True
            repair_call_id = f"{resume.run_id}:portfolio:repair"
            repaired = self.gateway.chat(self._portfolio_repair_request(
                sorted(expected_codes), response.content, checked.diagnostics, model_alias
            ))
            self._record(repaired, resume, repair_call_id, "pro_portfolio_summary_repair", PRO_PORTFOLIO_PROMPT_VERSION)
            checked = self._validate_portfolio(repaired, expected_codes)
            self.ledger.update_outcome(
                repair_call_id, status="SUCCESS" if checked.value else "FAILED",
                error_category=str(checked.diagnostics.get("error_category") or ""), diagnostics=checked.diagnostics,
            )
            responses.append(repaired)
        return checked.value, _stage_report("portfolio-summary", sorted(expected_codes), responses, checked, repair_attempted)

    def _record(
        self,
        response: LLMResponse,
        resume: ProResumeRun,
        call_id: str,
        task: str,
        prompt_version: str,
    ) -> None:
        self.ledger.record_response(response, UsageContext(
            call_id=call_id, pipeline_run_id=PIPELINE_RUN_ID,
            validation_run_id=resume.flash_validation_run_id,
            pro_resume_run_id=resume.run_id, agent_name="controller_agent",
            task=task, task_type="committee_controller", task_tier="HIGH_IMPACT",
            prompt_version=prompt_version,
        ))

    @staticmethod
    def _validate_candidate(response: LLMResponse, chunk_id: str, expected_codes: list[str]) -> ValidationResult:
        parsed, diagnostics = _parse_response(response)
        if parsed is None:
            return ValidationResult(None, diagnostics)
        try:
            wire = ProCandidateWireV2.model_validate(parsed)
        except ValidationError as exc:
            diagnostics.update(_pydantic_diagnostics(exc))
            return ValidationResult(None, diagnostics)
        if wire.chunk_id != chunk_id:
            return ValidationResult(None, {**diagnostics, "error_category": "CHUNK_ID_MISMATCH", "schema_error_paths": ["$.chunk_id"]})
        codes = [normalize_ts_code(item.stock_code) for item in wire.results]
        if len(codes) != len(set(codes)) or codes != expected_codes:
            return ValidationResult(None, {**diagnostics, "error_category": "CANDIDATE_CODE_SET_MISMATCH", "schema_error_paths": ["$.results[*].stock_code"]})
        for index, item in enumerate(wire.results):
            violation = scan_output_boundary(item.model_dump(mode="json"), expected_stock_code=expected_codes[index])
            if violation:
                return ValidationResult(None, {**diagnostics, "error_category": violation.category, "schema_error_paths": [f"$.results[{index}]{violation.path[1:]}"]})
            if any(len(text) > 160 for text in item.key_strengths + item.key_risks):
                return ValidationResult(None, {**diagnostics, "error_category": "TEXT_LENGTH_EXCEEDED", "schema_error_paths": [f"$.results[{index}]"]})
        diagnostics.update({"json_parse_status": "PASS", "schema_status": "PASS", "error_category": ""})
        normalized = [item.model_copy(update={"stock_code": code}) for item, code in zip(wire.results, codes)]
        return ValidationResult(wire.model_copy(update={"results": normalized}), diagnostics)

    @staticmethod
    def _validate_portfolio(response: LLMResponse, expected_codes: set[str]) -> ValidationResult:
        parsed, diagnostics = _parse_response(response)
        if parsed is None:
            return ValidationResult(None, diagnostics)
        if _forbidden_business_key(parsed):
            return ValidationResult(None, {**diagnostics, "error_category": "FORBIDDEN_PRICE_OR_POSITION_FIELD", "schema_error_paths": ["$"]})
        try:
            wire = ProPortfolioWireV2.model_validate(parsed)
        except ValidationError as exc:
            diagnostics.update(_pydantic_diagnostics(exc))
            return ValidationResult(None, diagnostics)
        try:
            top_codes = [normalize_ts_code(code) for code in wire.top_priority_stock_codes]
        except ValueError:
            return ValidationResult(None, {**diagnostics, "error_category": "PORTFOLIO_STOCK_CODE_INVALID", "schema_error_paths": ["$.top_priority_stock_codes"]})
        if len(top_codes) != len(set(top_codes)) or not set(top_codes).issubset(expected_codes):
            return ValidationResult(None, {**diagnostics, "error_category": "PORTFOLIO_EXTRA_STOCK", "schema_error_paths": ["$.top_priority_stock_codes"]})
        if _contains_url(wire.model_dump(mode="json")):
            return ValidationResult(None, {**diagnostics, "error_category": "FABRICATED_URL", "schema_error_paths": ["$"]})
        diagnostics.update({"json_parse_status": "PASS", "schema_status": "PASS", "error_category": ""})
        return ValidationResult(wire.model_copy(update={"top_priority_stock_codes": top_codes}), diagnostics)

    @staticmethod
    def _compact_candidate(sample: ModelValidationSample, sources: dict[str, str]) -> dict[str, Any]:
        code = normalize_ts_code(sample.stock_code)
        fundamental = sample.fundamental_result or {}
        screening = sample.screening_result or {}
        quant = sample.quant_scores or {}
        provenance = sample.field_provenance or {}
        chain = fundamental.get("industry_chain") or {}
        financial = fundamental.get("financial_status") or {}
        return {
            "stock_code": code,
            "stock_name": _bounded(sample.stock_name, 40),
            "selection_source": sources[code],
            "quant_rank": sample.rank,
            "quant_score": quant.get("total_score"),
            "five_quant_factors": {key: quant.get(key) for key in (
                "technical_score", "capital_score", "emotion_score", "momentum_score", "risk_score"
            )},
            "flash_score": screening.get("llm_score"),
            "flash_decision": screening.get("screening_decision"),
            "flash_confidence": screening.get("confidence"),
            "financial_status": financial.get("status") if isinstance(financial, dict) else str(financial),
            "observation_rating": fundamental.get("observation_rating") or "INSUFFICIENT_DATA",
            "industry": _bounded(_provenance_value(provenance, "level_one_sector") or "UNKNOWN", 80),
            "industry_chain": _bounded(chain.get("chain_name") or "UNKNOWN", 100),
            "main_business": _bounded(_inference_text(fundamental.get("main_business_summary")), 220),
            "competitive_advantage": _bounded(_inference_text(fundamental.get("competitive_advantage")), 180),
            "investment_logic": _bounded(_inference_text(fundamental.get("investment_logic")), 220),
            "domestic_substitution": _bounded(_inference_text(fundamental.get("domestic_substitution")), 100),
            "data_quality_score": screening.get("data_quality_score") or 0,
            "unverified_field_count": _unverified_count(fundamental),
            "hard_risk_status": financial.get("status") if isinstance(financial, dict) else "UNKNOWN",
            "manual_reason": _bounded((screening.get("_trader_demo") or {}).get("manual_reason") or "", 120),
        }

    @staticmethod
    def _portfolio_payload(
        candidates: list[ModelValidationSample],
        reviews: list[ProCandidateReview],
        sources: dict[str, str],
    ) -> dict[str, Any]:
        by_code = {normalize_ts_code(sample.stock_code): sample for sample in candidates}
        rows = []
        sectors: dict[str, int] = {}
        chains: dict[str, int] = {}
        risk_counts: dict[str, int] = {}
        quality_counts: dict[str, int] = {}
        for review in sorted(reviews, key=lambda item: int(item.pro_rank or 999)):
            sample = by_code[review.stock_code]
            fundamental = sample.fundamental_result or {}
            provenance = sample.field_provenance or {}
            sector = str(_provenance_value(provenance, "level_one_sector") or "UNKNOWN")
            chain = str((fundamental.get("industry_chain") or {}).get("chain_name") or "UNKNOWN")
            risk = str(((fundamental.get("financial_status") or {}).get("status") or "UNKNOWN"))
            sectors[sector] = sectors.get(sector, 0) + 1
            chains[chain] = chains.get(chain, 0) + 1
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
            quality_counts[review.fundamental_quality] = quality_counts.get(review.fundamental_quality, 0) + 1
            rows.append({
                "stock_code": review.stock_code, "stock_name": sample.stock_name,
                "selection_source": sources[review.stock_code], "pro_rank": review.pro_rank,
                "pro_score": float(review.pro_score), "priority": review.priority,
                "industry": sector, "industry_chain": chain, "risk_status": risk,
            })
        overlap = sum(source == "BOTH" for source in sources.values())
        return {
            "candidates": rows,
            "computed_statistics": {
                "sector_counts": sectors, "industry_chain_counts": chains,
                "selection_source_counts": {source: list(sources.values()).count(source) for source in sorted(set(sources.values()))},
                "manual_top20_overlap_count": overlap,
                "risk_status_counts": risk_counts,
                "fundamental_quality_counts": quality_counts,
            },
        }

    @staticmethod
    def _candidate_request(chunk_id: str, compact: list[dict[str, Any]], model_alias: str) -> LLMRequest:
        return LLMRequest(
            agent_name="controller_agent", task=f"pro_candidate_chunk:{chunk_id}",
            task_type="committee_controller", task_tier="HIGH_IMPACT", model_alias=model_alias,
            messages=[
                LLMMessage(role="system", content=(
                    "Review only the supplied candidate facts. Return one shallow JSON object matching wire_schema. "
                    "Do not output rank, prices, positions, URLs, news, customers, orders, market shares, or reasoning. "
                    "Preserve Quant, Flash, financial status, and the exact stock order."
                )),
                LLMMessage(role="user", content=json.dumps({
                    "schema_version": PRO_CONTRACT_VERSION, "chunk_id": chunk_id,
                    "wire_schema": candidate_wire_schema(), "candidates": compact,
                }, ensure_ascii=False, separators=(",", ":"), default=str)),
            ],
            max_tokens=1800, prompt_version=PRO_CANDIDATE_PROMPT_VERSION,
            json_mode=False, response_schema=None, allow_fallback=False,
            thinking_mode="enabled", reasoning_effort="high",
            metadata={"structured": True, "search_provider_enabled": False, "pro_contract": PRO_CONTRACT_VERSION},
        )

    @staticmethod
    def _candidate_repair_request(
        chunk_id: str,
        codes: list[str],
        candidate: str,
        diagnostics: dict[str, Any],
        model_alias: str,
    ) -> LLMRequest:
        return LLMRequest(
            agent_name="controller_agent", task=f"pro_candidate_chunk:{chunk_id}_repair",
            task_type="committee_controller", task_tier="HIGH_IMPACT", model_alias=model_alias,
            messages=[
                LLMMessage(role="system", content="Repair the candidate JSON only. Return JSON without commentary or reasoning."),
                LLMMessage(role="user", content=json.dumps({
                    "chunk_id": chunk_id, "expected_stock_codes": codes,
                    "error_category": diagnostics.get("error_category"),
                    "error_paths": diagnostics.get("schema_error_paths") or [],
                    "short_example": {
                        "schema_version": PRO_CONTRACT_VERSION, "chunk_id": chunk_id,
                        "results": [{
                            "stock_code": codes[0], "pro_score": 0, "priority": "REVIEW_ONLY",
                            "final_summary": "信息不足，需人工复核。", "key_strengths": [], "key_risks": [],
                            "fundamental_quality": "INSUFFICIENT", "quant_llm_consistency": "LOW",
                            "manual_review_priority": "HIGH", "data_conflict": False,
                        }],
                    },
                    "invalid_candidate": (candidate or "")[:12000],
                }, ensure_ascii=False, separators=(",", ":"))),
            ],
            max_tokens=1800, prompt_version=PRO_CANDIDATE_PROMPT_VERSION,
            json_mode=False, response_schema=None, allow_fallback=False,
            thinking_mode="enabled", reasoning_effort="high",
            metadata={"structured": True, "search_provider_enabled": False, "repair": True},
        )

    @staticmethod
    def _portfolio_request(payload: dict[str, Any], model_alias: str) -> LLMRequest:
        return LLMRequest(
            agent_name="controller_agent", task="pro_portfolio_summary",
            task_type="committee_controller", task_tier="HIGH_IMPACT", model_alias=model_alias,
            messages=[
                LLMMessage(role="system", content=(
                    "Summarize the supplied ranked candidate portfolio and precomputed statistics. "
                    "Return only the shallow JSON wire. Do not recalculate counts, create stocks, prices, positions, URLs, news, or reasoning."
                )),
                LLMMessage(role="user", content=json.dumps({
                    "wire_schema": portfolio_wire_schema(), **payload,
                }, ensure_ascii=False, separators=(",", ":"), default=str)),
            ],
            max_tokens=1400, prompt_version=PRO_PORTFOLIO_PROMPT_VERSION,
            json_mode=False, response_schema=None, allow_fallback=False,
            thinking_mode="enabled", reasoning_effort="max",
            metadata={"structured": True, "search_provider_enabled": False, "pro_contract": "pro_portfolio_wire_v2"},
        )

    @staticmethod
    def _portfolio_repair_request(
        codes: list[str],
        candidate: str,
        diagnostics: dict[str, Any],
        model_alias: str,
    ) -> LLMRequest:
        return LLMRequest(
            agent_name="controller_agent", task="pro_portfolio_summary_repair",
            task_type="committee_controller", task_tier="HIGH_IMPACT", model_alias=model_alias,
            messages=[
                LLMMessage(role="system", content="Repair the portfolio JSON only. Return JSON without commentary or reasoning."),
                LLMMessage(role="user", content=json.dumps({
                    "allowed_stock_codes": codes,
                    "error_category": diagnostics.get("error_category"),
                    "error_paths": diagnostics.get("schema_error_paths") or [],
                    "short_example": {
                        "schema_version": "pro_portfolio_wire_v2", "overall_summary": "需人工复核。",
                        "portfolio_risk_level": "REVIEW_REQUIRED", "sector_concentration_notes": [],
                        "industry_chain_concentration_notes": [], "manual_pool_notes": [],
                        "top_priority_stock_codes": [], "key_portfolio_risks": [],
                        "manual_review_focus": [], "data_conflict": False,
                    },
                    "invalid_candidate": (candidate or "")[:12000],
                }, ensure_ascii=False, separators=(",", ":"))),
            ],
            max_tokens=1400, prompt_version=PRO_PORTFOLIO_PROMPT_VERSION,
            json_mode=False, response_schema=None, allow_fallback=False,
            thinking_mode="enabled", reasoning_effort="max",
            metadata={"structured": True, "search_provider_enabled": False, "repair": True},
        )


def stable_candidate_order(samples: list[ModelValidationSample]) -> list[ModelValidationSample]:
    source_order = {"BOTH": 0, "LLM_TOP20": 1, "MANUAL": 2}
    return sorted(samples, key=lambda sample: (
        source_order[str((sample.screening_result or {}).get("_trader_demo", {}).get("selection_source") or "MANUAL")],
        -float((sample.screening_result or {}).get("llm_score") or -1),
        int(sample.rank),
        normalize_ts_code(sample.stock_code),
    ))


def chunk_candidates(samples: list[ModelValidationSample], chunk_size: int) -> list[list[ModelValidationSample]]:
    if chunk_size <= 0 or chunk_size > 5:
        raise ValueError("PRO_CHUNK_SIZE_INVALID")
    ordered = stable_candidate_order(samples)
    return [ordered[index:index + chunk_size] for index in range(0, len(ordered), chunk_size)]


def deterministic_review_order(reviews: list[Any], by_code: dict[str, Any]) -> list[Any]:
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(reviews, key=lambda row: (
        -float(row.pro_score),
        priority_order[row.manual_review_priority],
        -float((by_code[row.stock_code].screening_result or {}).get("llm_score") or 0),
        int(by_code[row.stock_code].rank),
        row.stock_code,
    ))


def _parse_response(response: LLMResponse) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    content = response.content or ""
    stripped = content.strip()
    diagnostics = {
        "provider_status": response.status,
        "http_status": (response.raw_response_metadata or {}).get("http_status"),
        "finish_reason": response.finish_reason,
        "content_empty": not bool(stripped), "content_length": len(content),
        "first_non_whitespace_character": stripped[:1],
        "last_non_whitespace_character": stripped[-1:] if stripped else "",
        "markdown_fence": stripped.startswith("```"),
        "brace_balance": content.count("{") - content.count("}"),
        "json_parse_status": "NOT_RUN", "schema_status": "NOT_RUN",
        "schema_error_paths": [], "error_count": 0,
    }
    if response.status not in {"ok", "SUCCESS"}:
        diagnostics["error_category"] = f"PROVIDER_{response.status.upper()}"
        return None, diagnostics
    if not stripped:
        diagnostics.update({"json_parse_status": "EMPTY", "error_category": "EMPTY_JSON_CONTENT"})
        return None, diagnostics
    if response.finish_reason in {"length", "max_tokens"}:
        diagnostics.update({"json_parse_status": "TRUNCATED", "error_category": "JSON_TRUNCATED"})
        return None, diagnostics
    text_value = stripped
    if text_value.startswith("```"):
        lines = text_value.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text_value = "\n".join(lines).strip()
    try:
        parsed = json.loads(text_value)
    except json.JSONDecodeError as exc:
        diagnostics.update({
            "json_parse_status": "INVALID_JSON", "error_category": "INVALID_JSON",
            "schema_error_paths": ["$"], "error_count": 1, "json_error_position": exc.pos,
        })
        return None, diagnostics
    if not isinstance(parsed, dict):
        diagnostics.update({"json_parse_status": "TOP_LEVEL_NOT_OBJECT", "error_category": "TOP_LEVEL_NOT_OBJECT", "schema_error_paths": ["$"]})
        return None, diagnostics
    diagnostics["json_parse_status"] = "PASS"
    return parsed, diagnostics


def _pydantic_diagnostics(exc: ValidationError) -> dict[str, Any]:
    paths = ["$" + "".join(f"[{item}]" if isinstance(item, int) else f".{item}" for item in error.get("loc") or []) for error in exc.errors()]
    return {
        "schema_status": "FAILED", "error_category": "SCHEMA_ERROR",
        "schema_error_paths": paths[:20], "error_count": len(exc.errors()),
        "extra_field_detected": any(error.get("type") == "extra_forbidden" for error in exc.errors()),
        "missing_field_detected": any(error.get("type") == "missing" for error in exc.errors()),
        "enum_error_detected": any(error.get("type") == "literal_error" for error in exc.errors()),
    }


def _stage_report(
    chunk_id: str,
    codes: list[str],
    responses: list[LLMResponse],
    checked: ValidationResult,
    repair_attempted: bool,
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id, "stock_count": len(codes), "stock_codes": codes,
        "actual_model": responses[-1].model, "schema_status": "PASS" if checked.value else "FAILED",
        "repair_attempted": repair_attempted,
        "input_tokens": sum(response.input_tokens for response in responses),
        "output_tokens": sum(response.output_tokens for response in responses),
        "latency_ms": sum(response.latency_ms for response in responses),
        "cost_usd": round(sum(float(response.cost_usd or 0) for response in responses), 8),
        "provider_status": responses[-1].status,
        "diagnostics": checked.diagnostics,
        "usage_persisted": True,
    }


def _forbidden_business_key(value: Any) -> bool:
    forbidden = re.compile(r"price|position|quantity|shares|stop_loss|take_profit|仓位|价格|股数", re.I)
    if isinstance(value, dict):
        return any(forbidden.search(str(key)) or _forbidden_business_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_forbidden_business_key(item) for item in value)
    return False


def _contains_url(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_url(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_url(item) for item in value)
    return bool(re.search(r"https?://|www\.", str(value or ""), re.I))


def _provenance_value(provenance: dict[str, Any], key: str) -> Any:
    value = provenance.get(key) or {}
    return value.get("value") if isinstance(value, dict) else value


def _inference_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("summary") or value.get("description") or value.get("level") or value.get("value") or "信息不足*")
    return str(value or "信息不足*")


def _bounded(value: Any, limit: int) -> str:
    text_value = re.sub(r"\s+", " ", str(value or "信息不足*")).strip()
    if len(text_value) <= limit:
        return text_value
    sentences = re.split(r"(?<=[。；.!?])", text_value)
    result = ""
    for sentence in sentences:
        if len(result) + len(sentence) > limit:
            break
        result += sentence
    return result or text_value[:limit]


def _unverified_count(value: Any) -> int:
    if isinstance(value, dict):
        return int(str(value.get("source_status") or "").upper() == "LLM_UNVERIFIED") + sum(_unverified_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_unverified_count(item) for item in value)
    return 0
