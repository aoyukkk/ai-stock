from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, inspect, select, text

from database.models.system import LLMUsage
from database.models.validation import ModelValidationLLMAudit
from database.session import get_session
from llm_gateway.schemas import LLMResponse


# Legacy replay scripts explicitly use this audited historical run identifier.
PIPELINE_RUN_ID = "daily-full-20260710"

USAGE_COLUMNS = {
    "call_id": "VARCHAR(128)",
    "pipeline_run_id": "VARCHAR(128)",
    "validation_run_id": "VARCHAR(128)",
    "pro_resume_run_id": "VARCHAR(128)",
    "usage_source": "VARCHAR(32)",
    "is_cached": "BOOLEAN NOT NULL DEFAULT 0",
    "is_reused": "BOOLEAN NOT NULL DEFAULT 0",
    "http_status": "INTEGER",
    "finish_reason": "VARCHAR(64)",
    "provider_request_id": "VARCHAR(128)",
    "response_hash": "VARCHAR(64)",
    "error_category": "VARCHAR(128)",
    "response_metadata": "JSON NOT NULL DEFAULT '{}'",
}


def ensure_usage_schema(engine: Engine) -> None:
    names = {column["name"] for column in inspect(engine).get_columns("llm_usage")}
    with engine.begin() as connection:
        for name, sql_type in USAGE_COLUMNS.items():
            if name not in names:
                connection.execute(text(f"ALTER TABLE llm_usage ADD COLUMN {name} {sql_type}"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_llm_usage_call_id ON llm_usage(call_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_llm_usage_pipeline_run_id ON llm_usage(pipeline_run_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_llm_usage_validation_run_id ON llm_usage(validation_run_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_llm_usage_pro_resume_run_id ON llm_usage(pro_resume_run_id)"))


@dataclass(frozen=True)
class UsageContext:
    call_id: str
    pipeline_run_id: str | None
    validation_run_id: str
    pro_resume_run_id: str
    agent_name: str
    task: str
    task_type: str
    task_tier: str
    prompt_version: str
    usage_source: str = "CURRENT_CALL"


class AuthoritativeUsageLedger:
    def __init__(self, engine: Engine, pipeline_run_id: str | None = None) -> None:
        self.engine = engine
        self.pipeline_run_id = pipeline_run_id
        ensure_usage_schema(engine)

    def record_response(self, response: LLMResponse, context: UsageContext) -> int:
        """Persist provider usage and safe metadata before business-schema parsing."""

        metadata = response.raw_response_metadata or {}
        safe_metadata = {
            "response_id_present": bool(metadata.get("response_id") or metadata.get("request_id")),
            "system_fingerprint_present": bool(metadata.get("system_fingerprint")),
            "reasoning_tokens": int(metadata.get("reasoning_tokens") or 0),
            "reasoning_stored": False,
            "content_length": len(response.content or ""),
            "content_empty": not bool((response.content or "").strip()),
        }
        session = get_session(self.engine)
        try:
            existing = session.scalar(select(LLMUsage).where(LLMUsage.call_id == context.call_id))
            if existing is not None:
                return existing.id
            row = LLMUsage(
                call_id=context.call_id,
                pipeline_run_id=context.pipeline_run_id,
                validation_run_id=context.validation_run_id,
                pro_resume_run_id=context.pro_resume_run_id,
                usage_source=context.usage_source,
                is_cached=bool(response.cached),
                is_reused=False,
                provider=response.provider,
                model_name=response.model,
                model_alias=response.model_alias,
                agent_name=context.agent_name,
                task=context.task,
                task_type=context.task_type,
                task_tier=context.task_tier,
                thinking_mode=response.thinking_mode,
                reasoning_effort=response.reasoning_effort,
                prompt_version=context.prompt_version,
                input_tokens=response.input_tokens,
                input_cache_hit_tokens=response.input_cache_hit_tokens,
                input_cache_miss_tokens=response.input_cache_miss_tokens,
                output_tokens=response.output_tokens,
                cached_input_tokens=response.input_tokens if response.cached else 0,
                total_tokens=response.total_tokens,
                cost_usd=response.cost_usd,
                cost_status=response.cost_status,
                pricing_version=response.pricing_version,
                latency_ms=response.latency_ms,
                status=response.status,
                http_status=_int_or_none(metadata.get("http_status")),
                finish_reason=response.finish_reason,
                provider_request_id=str(metadata.get("response_id") or metadata.get("request_id") or "") or None,
                response_hash=hashlib.sha256((response.content or "").encode("utf-8")).hexdigest(),
                error_category=None,
                response_metadata=safe_metadata,
                error_message=None,
                request_hash=response.request_hash,
            )
            session.add(row)
            session.commit()
            return row.id
        finally:
            session.close()

    def update_outcome(
        self,
        call_id: str,
        *,
        status: str,
        error_category: str = "",
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        session = get_session(self.engine)
        try:
            row = session.scalar(select(LLMUsage).where(LLMUsage.call_id == call_id))
            if row is None:
                raise ValueError(f"LLM_USAGE_CALL_NOT_FOUND:{call_id}")
            row.status = status
            row.error_category = error_category or None
            row.response_metadata = {**dict(row.response_metadata or {}), **(diagnostics or {})}
            session.commit()
        finally:
            session.close()

    def backfill_flash_and_legacy(
        self,
        session,
        *,
        flash_validation_run_id: str,
        connectivity_total_tokens: int,
    ) -> dict[str, int]:
        audits = list(session.scalars(
            select(ModelValidationLLMAudit).where(
                ModelValidationLLMAudit.validation_run_id == flash_validation_run_id,
                ModelValidationLLMAudit.task.in_([
                    "fundamental_structured_inference", "structured_light_screening"
                ]),
            )
        ))
        effective: dict[int, ModelValidationLLMAudit] = {}
        for audit in audits:
            source = self._resolve_source_audit(session, audit)
            effective[source.id] = source
        for audit in effective.values():
            repair_input = int((audit.diagnostics or {}).get("repair_input_tokens") or 0)
            repair_output = int((audit.diagnostics or {}).get("repair_output_tokens") or 0)
            self._insert_backfill(
                call_id=f"flash-audit-{audit.id}-base",
                validation_run_id=audit.validation_run_id,
                task=audit.task,
                prompt_version=audit.prompt_version,
                provider="deepseek",
                model=audit.actual_model,
                input_tokens=max(0, audit.input_tokens - repair_input),
                output_tokens=max(0, audit.output_tokens - repair_output),
                total_tokens=max(0, audit.input_tokens + audit.output_tokens - repair_input - repair_output),
                cost_usd=audit.cost_usd,
                status=audit.status,
                request_hash=audit.request_hash,
                usage_source="HISTORICAL_API_USAGE",
                metadata={"source_audit_id": audit.id, "schema_status": audit.schema_status},
            )
            if repair_input or repair_output:
                self._insert_backfill(
                    call_id=f"flash-audit-{audit.id}-repair",
                    validation_run_id=audit.validation_run_id,
                    task=f"{audit.task}_repair",
                    prompt_version=audit.prompt_version,
                    provider="deepseek",
                    model=audit.actual_model,
                    input_tokens=repair_input,
                    output_tokens=repair_output,
                    total_tokens=repair_input + repair_output,
                    cost_usd=Decimal("0"),
                    status=audit.status,
                    request_hash=f"{audit.request_hash or audit.id}:repair",
                    usage_source="HISTORICAL_API_USAGE",
                    metadata={"source_audit_id": audit.id, "repair_usage_split": True},
                )
        self._insert_backfill(
            call_id="legacy-connectivity-aggregate-20260710",
            validation_run_id="",
            task="connectivity_test",
            prompt_version="deepseek-connectivity-canary-v1",
            provider="deepseek",
            model="deepseek-v4-flash",
            input_tokens=None,
            output_tokens=None,
            total_tokens=connectivity_total_tokens,
            cost_usd=None,
            status="SUCCESS",
            request_hash="legacy-connectivity-aggregate-20260710",
            usage_source="LEGACY_AGGREGATE",
            metadata={"component_split_available": False, "aggregate_token_count_verified": True},
        )
        self._insert_backfill(
            call_id="legacy-pro-failure-20260710",
            validation_run_id=flash_validation_run_id,
            task="daily_pro_aggregation_legacy",
            prompt_version="daily-pro-aggregation-v1",
            provider="deepseek",
            model="deepseek-v4-pro",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            cost_usd=None,
            status="FAILED",
            request_hash="legacy-pro-failure-usage-unavailable",
            usage_source="UNAVAILABLE",
            metadata={"error_category": "LEGACY_PRO_FAILURE_DIAGNOSTICS_INCOMPLETE"},
        )
        return self.summary()

    def summary(self, *, pro_resume_run_id: str | None = None) -> dict[str, int | float]:
        session = get_session(self.engine)
        try:
            rows = list(session.scalars(select(LLMUsage).where(LLMUsage.pipeline_run_id == self.pipeline_run_id)))
        finally:
            session.close()
        historical = [row for row in rows if row.usage_source == "HISTORICAL_API_USAGE"]
        current = [row for row in rows if pro_resume_run_id and row.pro_resume_run_id == pro_resume_run_id]
        connectivity = [row for row in rows if row.task == "connectivity_test"]
        flash_repair = [row for row in historical if str(row.task or "").endswith("_repair")]
        flash_base = [row for row in historical if row not in flash_repair]
        candidate = [row for row in current if (
            (str(row.task or "").startswith("pro_candidate_chunk") and not str(row.task or "").endswith("_repair"))
            or row.task == "pro_candidate_single_review"
        )]
        candidate_repair = [row for row in current if (
            (str(row.task or "").startswith("pro_candidate_chunk") and str(row.task or "").endswith("_repair"))
            or row.task == "pro_candidate_single_review_repair"
        )]
        portfolio = [row for row in current if row.task in {"pro_portfolio_summary", "pro_portfolio_v3"}]
        portfolio_repair = [row for row in current if row.task in {"pro_portfolio_summary_repair", "pro_portfolio_v3_repair"}]
        actual_rows = [row for row in rows if row.total_tokens is not None and row.usage_source != "UNAVAILABLE"]
        return {
            "connectivity_actual": _sum_tokens(connectivity),
            "flash_actual": _sum_tokens(flash_base),
            "flash_repair_actual": _sum_tokens(flash_repair),
            "pro_candidate_actual": _sum_tokens(candidate),
            "pro_candidate_repair_actual": _sum_tokens(candidate_repair),
            "pro_portfolio_actual": _sum_tokens(portfolio),
            "pro_portfolio_repair_actual": _sum_tokens(portfolio_repair),
            "current_resume_new_tokens": _sum_tokens(current),
            "historical_reused_tokens": _sum_tokens(historical),
            "total_actual_api_tokens": _sum_tokens(actual_rows),
            "unavailable_usage_count": sum(row.usage_source == "UNAVAILABLE" for row in rows),
            "total_cost_usd": round(sum(float(row.cost_usd or 0) for row in actual_rows), 8),
        }

    def _insert_backfill(self, **values: Any) -> None:
        session = get_session(self.engine)
        try:
            if session.scalar(select(LLMUsage.id).where(LLMUsage.call_id == values["call_id"])) is not None:
                return
            session.add(LLMUsage(
                call_id=values["call_id"], pipeline_run_id=self.pipeline_run_id,
                validation_run_id=values["validation_run_id"] or None,
                pro_resume_run_id=None, usage_source=values["usage_source"],
                is_cached=False, is_reused=False, provider=values["provider"],
                model_name=values["model"], model_alias=None, agent_name="model_validation",
                task=values["task"], task_type=values["task"], task_tier=None,
                thinking_mode=None, reasoning_effort=None, prompt_version=values["prompt_version"],
                input_tokens=values["input_tokens"], input_cache_hit_tokens=0,
                input_cache_miss_tokens=values["input_tokens"], output_tokens=values["output_tokens"],
                cached_input_tokens=0, total_tokens=values["total_tokens"], cost_usd=values["cost_usd"],
                cost_status="LEGACY_BACKFILL", pricing_version=None, latency_ms=None,
                status=values["status"], http_status=None, finish_reason=None,
                provider_request_id=None, response_hash=None, error_category=None,
                response_metadata=values["metadata"], error_message=None,
                request_hash=values["request_hash"],
            ))
            session.commit()
        finally:
            session.close()

    @staticmethod
    def _resolve_source_audit(session, audit: ModelValidationLLMAudit) -> ModelValidationLLMAudit:
        current = audit
        visited: set[str] = set()
        while current.cache_status == "REUSED":
            source_run = str((current.diagnostics or {}).get("source_validation_run_id") or "")
            if not source_run or source_run in visited:
                raise ValueError(f"REUSED_USAGE_SOURCE_INVALID:{audit.stock_code}:{audit.task}")
            visited.add(source_run)
            current = session.scalar(
                select(ModelValidationLLMAudit)
                .where(
                    ModelValidationLLMAudit.validation_run_id == source_run,
                    ModelValidationLLMAudit.stock_code == audit.stock_code,
                    ModelValidationLLMAudit.task == audit.task,
                    ModelValidationLLMAudit.schema_status == "PASS",
                )
                .order_by(ModelValidationLLMAudit.id.desc())
            )
            if current is None:
                raise ValueError(f"REUSED_USAGE_SOURCE_MISSING:{audit.stock_code}:{audit.task}")
        return current


def _sum_tokens(rows: list[LLMUsage]) -> int:
    return sum(int(row.total_tokens or 0) for row in rows)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
