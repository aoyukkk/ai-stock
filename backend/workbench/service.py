from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from database.models.quant_run import QuantRankResult, QuantRun
from database.models.research import ResearchEvidenceRecord
from database.models.stock import StockMaster
from database.models.system import ConfigHistory, LLMUsage, SystemConfig
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationLLMAudit,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
    ProCandidateReview,
    ProResumeRun,
)
from database.models.workbench import ManualSelectionRecord, PipelineJob
from database.session import assert_database_path_consistency, get_database_identity, get_database_url
from backend.workbench.historical import HistoricalPipelineRunResolver
from stock_codes import display_stock_code, normalize_ts_code
from trader_demo.budget import PipelineBudgetConfig
from market_review.repository import MarketReviewRepository


ACTIVE_JOB_STATUSES = {"PENDING", "RUNNING"}
JOB_TYPES = {"DATA", "QUANT", "FLASH", "FINAL", "EXPORT", "MARKET_DAILY_REVIEW"}
SECRET_ENV = {
    "tushare": "TUSHARE_TOKEN",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "ifind_username": "IFIND_USERNAME",
    "ifind_password": "IFIND_PASSWORD",
    "ifind_access": "IFIND_ACCESS_TOKEN",
    "ifind_refresh": "IFIND_REFRESH_TOKEN",
}
SETTINGS_DEFAULTS = {
    "quant_top_n": 100,
    "llm_analysis_n": 100,
    "llm_top_n": 20,
    "final_display_n": 20,
    "manual_soft_limit": 50,
    "manual_max_limit": 100,
    "market_review_enabled": True,
    "market_review_auto_run": True,
    "market_review_search_enabled": True,
    "market_review_search_provider": "AUTO",
    "market_review_max_queries": 12,
    "market_review_max_results_per_query": 8,
    "market_review_max_age_hours": 36,
    "market_review_official_source_priority": True,
    "market_review_multi_source_count": 2,
    "market_review_evidence_min_confidence": 0.55,
    "market_review_include_outlook": True,
    "market_review_pro_enabled": False,
    "market_review_pro_model_alias": "controller-high-capability",
    "market_review_pro_max_tokens": 8000,
    "market_review_auto_update_excel": True,
    "market_review_allow_data_only_fallback": True,
}


class WorkbenchService:
    """Thin orchestration/readback layer. It never changes scoring algorithms."""

    def __init__(self, session) -> None:
        self.session = session
        self.resolver = HistoricalPipelineRunResolver(session)

    def status(self, trade_date: date, pipeline_run_id: str | None = None) -> dict[str, Any]:
        assert_database_path_consistency(get_database_url(), str(self.session.get_bind().url))
        bundle = self.resolver.resolve(trade_date, pipeline_run_id)
        stages = bundle.get("stages") or {}
        empty_stage = {"status": "EMPTY", "run_id": None, "updated_at": None, "count": 0}
        market_review = MarketReviewRepository(self.session).latest_run(trade_date)
        return bundle | {
            "data": stages.get("data", empty_stage),
            "quant": stages.get("quant", empty_stage),
            "flash": stages.get("flash", empty_stage),
            "manual": stages.get("manual", empty_stage),
            "final": stages.get("final", empty_stage),
            "export": stages.get("export", empty_stage),
            "market_review": ({
                "status": market_review.status,
                "run_id": market_review.run_id,
                "updated_at": market_review.completed_at,
                "count": 1,
            } if market_review else empty_stage),
            "manual_count": int((bundle.get("counts") or {}).get("manual", 0)),
            "token": bundle["token_usage"],
            "flash_budget": self._flash_budget(trade_date),
            "providers": {key: {"configured": bool(os.getenv(env))} for key, env in SECRET_ENV.items()},
            "database": {"status": "READY", **get_database_identity()},
            "mode": "HISTORICAL_READBACK",
            "real_trading_enabled": False,
            "latest_run_ids": {
                "pipeline_run_id": bundle.get("pipeline_run_id"),
                "quant_run_id": bundle.get("quant_run_id"),
                "flash_run_id": bundle.get("flash_run_id"),
                "pro_run_id": bundle.get("pro_run_id"),
            },
        }

    def _flash_budget(self, trade_date: date) -> dict[str, int | float]:
        used = int(self.session.scalar(
            select(func.coalesce(func.sum(
                ModelValidationLLMAudit.input_tokens + ModelValidationLLMAudit.output_tokens
            ), 0))
            .join(
                ModelValidationRun,
                ModelValidationRun.run_id == ModelValidationLLMAudit.validation_run_id,
            )
            .where(
                ModelValidationRun.base_market_trade_date == trade_date,
            )
        ) or 0)
        limit = PipelineBudgetConfig().flash_limit
        return {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "usage_ratio": round(used / limit, 6) if limit else 1.0,
        }

    def available_dates(self) -> list[dict[str, Any]]:
        return self.resolver.available_dates()

    def available_runs(self, trade_date: date) -> list[dict[str, Any]]:
        return [{
            "pipeline_run_id": item["pipeline_run_id"],
            "quant_run_id": item["quant"].run_id,
            "flash_run_id": item["flash"].run_id,
            "pro_run_id": item["pro"].run_id,
            "candidate_set_hash": item["pro"].candidate_set_hash,
            "completed_at": item["pro"].updated_at or item["pro"].created_at,
        } for item in self.resolver.compatible_runs(trade_date)]

    def reconcile(self, trade_date: date, pipeline_run_id: str | None = None) -> dict[str, Any]:
        return self.resolver.reconcile(trade_date, pipeline_run_id)

    def data_check(self, trade_date: date) -> dict[str, Any]:
        """Report only locally persisted run evidence; this method never contacts a provider."""
        bundle = self.resolver.resolve(trade_date)
        quant = self.session.scalar(select(QuantRun).where(QuantRun.run_id == bundle.get("quant_run_id")))
        ready = bool(quant and quant.temporal_status == "PASS" and bundle["source_mode"] == "DATABASE")
        return {
            "trade_date": trade_date.isoformat(),
            "scenario": "COMPLETE" if ready else "MISSING",
            "temporal_gate": quant.temporal_status if quant else "NOT_RUN",
            "coverage": 1.0 if ready else 0.0,
            "duplicate_count": 0,
            "datasets": [{
                "dataset": "quant_input_snapshot",
                "status": "COMPLETE" if ready else "MISSING",
                "source": "LOCAL_DATABASE",
                "run_id": quant.run_id if quant else None,
            }],
            "provider_contacted": False,
        }

    def settings(self) -> dict[str, Any]:
        return {key: self._setting(key, value) for key, value in SETTINGS_DEFAULTS.items()} | {
            "daily_token_limit": self._setting("daily_token_limit", 5_000_000),
            "token_warning_ratio": self._setting("token_warning_ratio", 0.8),
            "flash_concurrency": self._setting("flash_concurrency", 5),
            "flash_batch_size": self._setting("flash_batch_size", 10),
            "reuse_cache": self._setting("reuse_cache", True),
            "validation_account_equity": self._setting("validation_account_equity", 1_000_000),
            "validation_available_cash": self._setting("validation_available_cash", 1_000_000),
        }

    def update_settings(self, values: dict[str, Any], *, user: str = "local_trader") -> dict[str, Any]:
        allowed = set(SETTINGS_DEFAULTS) | {
            "daily_token_limit", "token_warning_ratio", "flash_concurrency", "flash_batch_size",
            "reuse_cache", "validation_account_equity", "validation_available_cash",
        }
        if set(values) - allowed:
            raise ValueError("WORKBENCH_SETTING_NOT_ALLOWED")
        merged = self.settings() | values
        _validate_settings(merged)
        for key, value in values.items():
            config_key = f"workbench.{key}"
            row = self.session.scalar(select(SystemConfig).where(SystemConfig.config_key == config_key))
            old = row.config_value if row else None
            if row is None:
                row = SystemConfig(config_key=config_key, config_value=value, value_type=type(value).__name__, category="workbench")
                self.session.add(row)
            else:
                row.config_value = value
                row.value_type = type(value).__name__
            self.session.add(ConfigHistory(user=user, config_key=config_key, old_value={"value": old}, new_value={"value": value}, reason="Workbench setting update", time=datetime.now(timezone.utc)))
        self.session.commit()
        return self.settings()

    def secret_status(self) -> dict[str, Any]:
        if _server_mode():
            from backend.core.dpapi_secret_store import WindowsDpapiSecretStore

            status = WindowsDpapiSecretStore().status(list(SECRET_ENV))
            return {name: status[name] | {"last_test_status": "NOT_TESTED", "last_test_at": None} for name in SECRET_ENV}
        return {name: {"configured": bool(os.getenv(env)), "last_test_status": "NOT_TESTED", "last_test_at": None} for name, env in SECRET_ENV.items()}

    def set_secret(self, provider: str, value: str) -> dict[str, Any]:
        env = SECRET_ENV.get(provider)
        minimum_length = 1 if provider == "ifind_username" else 8
        if env is None or len(value.strip()) < minimum_length:
            raise ValueError("INVALID_SECRET_REQUEST")
        if _server_mode():
            from backend.core.dpapi_secret_store import WindowsDpapiSecretStore

            WindowsDpapiSecretStore().set(provider, value.strip())
        os.environ[env] = value.strip()
        return {"provider": provider, "configured": True}

    def delete_secret(self, provider: str) -> dict[str, Any]:
        env = SECRET_ENV.get(provider)
        if env is None:
            raise ValueError("UNKNOWN_SECRET_PROVIDER")
        if _server_mode():
            from backend.core.dpapi_secret_store import WindowsDpapiSecretStore

            WindowsDpapiSecretStore().delete(provider)
        os.environ.pop(env, None)
        return {"provider": provider, "configured": False}

    def test_secret(self, provider: str) -> dict[str, Any]:
        if provider not in SECRET_ENV:
            raise ValueError("UNKNOWN_SECRET_PROVIDER")
        configured = self.secret_status()[provider]["configured"]
        return {"provider": provider, "configured": configured, "status": "READY" if configured else "NOT_CONFIGURED"}

    def list_quant(self, trade_date: date, *, page: int, page_size: int, keyword: str = "", only_top: bool = False,
                   only_manual: bool = False, only_candidate: bool = False, quant_run_id: str | None = None,
                   pipeline_run_id: str | None = None, sort_by: str = "rank", sort_order: str = "asc") -> dict[str, Any]:
        bundle = self.resolver.resolve(trade_date, pipeline_run_id)
        resolved_run_id = quant_run_id or bundle.get("quant_run_id")
        if not resolved_run_id:
            return _page([], 0, page, page_size)
        sort_fields = {"rank": QuantRankResult.rank, "total_score": QuantRankResult.total_score, "stock_code": QuantRankResult.stock_code}
        sort_field = sort_fields.get(sort_by, QuantRankResult.rank)
        order = sort_field.desc() if sort_order.lower() == "desc" else sort_field.asc()
        statement = select(QuantRankResult).where(QuantRankResult.quant_run_id == resolved_run_id).order_by(order)
        rows = list(self.session.scalars(statement))
        names = self._stock_names(rows)
        manual = self._bundle_manual_codes(bundle)
        items = [{
            "rank": row.rank, "stock_code": display_stock_code(row.stock_code), "stock_name": names.get(normalize_ts_code(row.stock_code), ""),
            "total_score": float(row.total_score), "technical_score": float(row.technical_score), "capital_score": float(row.capital_score),
            "emotion_score": float(row.emotion_score), "momentum_score": float(row.momentum_score), "risk_score": float(row.risk_score),
            "manual_selected": normalize_ts_code(row.stock_code) in manual,
        } for row in rows]
        if keyword:
            needle = keyword.lower()
            items = [item for item in items if needle in item["stock_code"].lower() or needle in item["stock_name"].lower()]
        if only_top:
            items = [item for item in items if item["rank"] <= self._setting("quant_top_n", 100)]
        candidate_codes = self._bundle_candidate_codes(bundle)
        if only_manual:
            items = [item for item in items if item["manual_selected"]]
        if only_candidate:
            items = [item for item in items if normalize_ts_code(item["stock_code"]) in candidate_codes]
        return _page(items, len(items), page, page_size, run_id=resolved_run_id)

    def list_flash(self, trade_date: date, *, page: int, page_size: int, flash_run_id: str | None = None,
                   pipeline_run_id: str | None = None, keyword: str = "", sort_by: str = "rank",
                   sort_order: str = "asc") -> dict[str, Any]:
        bundle = self.resolver.resolve(trade_date, pipeline_run_id)
        resolved_run_id = flash_run_id or bundle.get("flash_run_id")
        if not resolved_run_id:
            return _page([], 0, page, page_size)
        rows = list(self.session.scalars(select(ModelValidationSample).where(ModelValidationSample.validation_run_id == resolved_run_id).order_by(ModelValidationSample.rank)))
        names = self._stock_names(rows)
        manual = self._bundle_manual_codes(bundle)
        items = []
        for row in rows:
            screening = row.screening_result or {}
            meta = screening.get("_trader_demo") or {}
            items.append({
                "rank": row.rank, "stock_code": display_stock_code(row.stock_code), "stock_name": names.get(normalize_ts_code(row.stock_code), ""),
                "quant_score": _number((row.quant_scores or {}).get("total_score")), "flash_score": _number(screening.get("llm_score")),
                "decision": screening.get("screening_decision"), "confidence": _number(screening.get("confidence")),
                "quant_consistency_score": _number(screening.get("quant_consistency_score")),
                "fundamental_quality_score": _number(screening.get("fundamental_quality_score")),
                "financial_quality_score": _number(screening.get("financial_quality_score")),
                "risk_fit_score": _number(screening.get("risk_fit_score")), "data_quality_score": _number(screening.get("data_quality_score")),
                "llm_selected": bool(meta.get("llm_selected")), "manual_selected": normalize_ts_code(row.stock_code) in manual,
                "execution_status": meta.get("execution_status"), "error_category": _error_category(meta),
            })
        if keyword:
            needle = keyword.lower()
            items = [item for item in items if needle in item["stock_code"].lower() or needle in item["stock_name"].lower()]
        allowed_sort = {"rank", "stock_code", "quant_score", "flash_score", "confidence", "execution_status"}
        actual_sort = sort_by if sort_by in allowed_sort else "rank"
        items.sort(
            key=lambda item: (item.get(actual_sort) is None, item.get(actual_sort)),
            reverse=sort_order.lower() == "desc",
        )
        return _page(items, len(items), page, page_size, run_id=resolved_run_id)

    def final_results(self, trade_date: date, pipeline_run_id: str | None = None) -> list[dict[str, Any]]:
        bundle = self.resolver.resolve(trade_date, pipeline_run_id)
        flash_run_id, pro_run_id = bundle.get("flash_run_id"), bundle.get("pro_run_id")
        if not flash_run_id or not pro_run_id:
            return []
        reviews = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(
            select(ProCandidateReview).where(ProCandidateReview.pro_resume_run_id == pro_run_id)
        )}
        pro = self.session.scalar(select(ProResumeRun).where(ProResumeRun.run_id == pro_run_id))
        sources = dict((pro.config_snapshot or {}).get("selection_sources") or {}) if pro else {}
        rows = self.session.scalars(select(ModelValidationSample).where(ModelValidationSample.validation_run_id == flash_run_id)).all()
        names = self._stock_names(rows)
        result = []
        for row in rows:
            screening = row.screening_result or {}
            source = sources.get(normalize_ts_code(row.stock_code)) or sources.get(row.stock_code)
            if not source:
                continue
            review = reviews.get(normalize_ts_code(row.stock_code))
            result.append({
                "final_rank": review.pro_rank if review else None, "stock_code": display_stock_code(row.stock_code),
                "stock_name": names.get(normalize_ts_code(row.stock_code), ""), "source": source,
                "pro_score": _number(review.pro_score) if review else None, "pro_priority": review.priority if review else None,
                "flash_score": _number(screening.get("llm_score")), "flash_decision": screening.get("screening_decision"),
                "quant_rank": row.rank, "quant_score": _number((row.quant_scores or {}).get("total_score")),
                "financial_status": _fundamental_status(row.fundamental_result or {}),
                "summary": review.final_summary if review else "",
            })
        return sorted(result, key=lambda item: (item["final_rank"] is None, item["final_rank"] or 9999, item["stock_code"]))

    def order_position_results(self, trade_date: date, pipeline_run_id: str | None = None) -> list[dict[str, Any]]:
        bundle = self.resolver.resolve(trade_date, pipeline_run_id)
        run_id = bundle.get("flash_run_id")
        if not run_id:
            return []
        plans = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(select(ModelValidationOrderPlan).where(ModelValidationOrderPlan.validation_run_id == run_id))}
        allocations = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(select(ModelValidationAllocation).where(ModelValidationAllocation.validation_run_id == run_id))}
        final = {normalize_ts_code(item["stock_code"]): item for item in self.final_results(trade_date, pipeline_run_id)}
        result = []
        for code, item in final.items():
            plan, allocation = plans.get(code), allocations.get(code)
            result.append({
                **item,
                "conservative_price": _number(getattr(plan, "conservative_price", None)), "balanced_price": _number(getattr(plan, "balanced_price", None)),
                "recommended_price": _number(getattr(plan, "recommended_price", None)), "stop_loss_price": _number(getattr(plan, "stop_loss_price", None)),
                "take_profit_1": _number(getattr(plan, "take_profit_1", None)), "take_profit_2": _number(getattr(plan, "take_profit_2", None)),
                "risk_reward": _number(getattr(plan, "active_risk_reward", None)), "position_percent": _number(getattr(allocation, "suggested_position_percent", None)),
                "suggested_capital": _number(getattr(allocation, "suggested_capital_amount", None)), "suggested_quantity": getattr(allocation, "suggested_quantity", 0),
                "max_loss": _number(getattr(allocation, "estimated_max_loss", None)), "status": getattr(plan, "status", "NOT_READY"),
                "warnings": list(getattr(plan, "warnings", []) or []) + list(getattr(allocation, "warnings", []) or []),
            })
        return result

    def fundamentals(self, trade_date: date, pipeline_run_id: str | None = None) -> list[dict[str, Any]]:
        bundle = self.resolver.resolve(trade_date, pipeline_run_id)
        run_id = bundle.get("flash_run_id")
        if not run_id:
            return []
        final = {normalize_ts_code(item["stock_code"]): item for item in self.final_results(trade_date, pipeline_run_id)}
        rows = self.session.scalars(select(ModelValidationSample).where(ModelValidationSample.validation_run_id == run_id)).all()
        research_run_ids = {
            str(((row.fundamental_result or {}).get("external_research_audit") or {}).get("research_run_id"))
            for row in rows
            if ((row.fundamental_result or {}).get("external_research_audit") or {}).get("research_run_id")
        }
        evidence_by_run: dict[str, list[ResearchEvidenceRecord]] = {}
        if research_run_ids:
            for evidence in self.session.scalars(select(ResearchEvidenceRecord).where(
                ResearchEvidenceRecord.run_id.in_(research_run_ids)
            ).order_by(ResearchEvidenceRecord.id)):
                evidence_by_run.setdefault(evidence.run_id, []).append(evidence)
        result = []
        for row in rows:
            code = normalize_ts_code(row.stock_code)
            if code not in final:
                continue
            fundamental = row.fundamental_result or {}
            audit = fundamental.get("external_research_audit") or {}
            evidence = evidence_by_run.get(str(audit.get("research_run_id") or ""), [])
            result.append({
                **final[code], "industry_chain": _field(fundamental.get("industry_chain"), "chain_name"),
                "chain_position": _field(fundamental.get("industry_chain"), "chain_position"),
                "chain_position_label": _status_label(
                    _field(fundamental.get("industry_chain"), "chain_position")
                ),
                "main_business": _field(fundamental.get("main_business_summary"), "summary"),
                "core_products": fundamental.get("core_products") or [],
                "concept_tags": _concept_names(fundamental.get("concept_tags") or []),
                "investment_logic": _field(fundamental.get("investment_logic"), "summary"), "financial_status": _fundamental_status(fundamental),
                "analysis_status": fundamental.get("analysis_status") or "UNKNOWN",
                "analysis_status_label": _status_label(fundamental.get("analysis_status")),
                "research_mode": fundamental.get("research_mode") or "UNKNOWN",
                "research_mode_label": _status_label(fundamental.get("research_mode")),
                "financial_status_label": _status_label(_fundamental_status(fundamental)),
                "as_of_time": fundamental.get("as_of_time"),
                "financial_summary": _financial_snapshot_summary(fundamental.get("financial_snapshot") or {}),
                "key_risks": fundamental.get("key_risks") or [],
                "evidence_count": len(evidence),
                "evidence_sources": [item.title for item in evidence],
                "evidence_urls": [item.url for item in evidence],
            })
        return result

    def manual_snapshot(self, trade_date: date, pipeline_run_id: str | None = None) -> list[dict[str, Any]]:
        bundle = self.resolver.resolve(trade_date, pipeline_run_id)
        pro_run_id = bundle.get("pro_run_id")
        if not pro_run_id:
            return []
        pro = self.session.scalar(select(ProResumeRun).where(ProResumeRun.run_id == pro_run_id))
        sources = dict((pro.config_snapshot or {}).get("selection_sources") or {}) if pro else {}
        codes = {normalize_ts_code(code) for code, source in sources.items() if source in {"MANUAL", "BOTH"}}
        samples = list(self.session.scalars(select(ModelValidationSample).where(
            ModelValidationSample.validation_run_id == bundle["flash_run_id"]
        )))
        names = self._stock_names(samples)
        return [{
            "stock_code": display_stock_code(code), "stock_name": names.get(code, ""),
            "source": sources.get(code, "MANUAL"), "read_only": True,
            "flash_run_id": bundle["flash_run_id"], "pro_run_id": pro_run_id,
        } for code in sorted(codes)]

    def manual_selections(self, trade_date: date) -> list[dict[str, Any]]:
        rows = list(self.session.scalars(select(ManualSelectionRecord).where(ManualSelectionRecord.trade_date == trade_date).order_by(ManualSelectionRecord.created_at.desc())))
        return [{"id": row.id, "stock_code": display_stock_code(row.stock_code), "reason": row.reason, "priority": row.priority, "selected_by": row.selected_by, "created_at": row.created_at, "updated_at": row.updated_at} for row in rows]

    def add_manual(self, trade_date: date, stock_code: str, reason: str, priority: str, quant_run_id: str | None) -> dict[str, Any]:
        code = normalize_ts_code(stock_code)
        row = self.session.scalar(select(ManualSelectionRecord).where(ManualSelectionRecord.trade_date == trade_date, ManualSelectionRecord.stock_code == code))
        if row is None:
            row = ManualSelectionRecord(trade_date=trade_date, stock_code=code, reason=reason.strip(), priority=_priority(priority), quant_run_id=quant_run_id)
            self.session.add(row)
        else:
            row.reason, row.priority, row.quant_run_id = reason.strip(), _priority(priority), quant_run_id
        self.session.commit()
        return {"id": row.id, "stock_code": display_stock_code(row.stock_code), "reason": row.reason, "priority": row.priority}

    def update_manual(self, selection_id: int, reason: str, priority: str) -> dict[str, Any]:
        row = self.session.get(ManualSelectionRecord, selection_id)
        if row is None:
            raise ValueError("MANUAL_SELECTION_NOT_FOUND")
        row.reason, row.priority = reason.strip(), _priority(priority)
        self.session.commit()
        return {"id": row.id, "stock_code": display_stock_code(row.stock_code), "reason": row.reason, "priority": row.priority}

    def add_manual_batch(self, trade_date: date, stock_codes: list[str], reason: str, priority: str, quant_run_id: str | None) -> dict[str, Any]:
        normalized = list(dict.fromkeys(normalize_ts_code(code) for code in stock_codes if code.strip()))
        if not normalized:
            raise ValueError("MANUAL_SELECTION_CODES_REQUIRED")
        if len(normalized) > int(self._setting("manual_max_limit", 100)):
            raise ValueError("MANUAL_SELECTION_MAX_LIMIT_EXCEEDED")
        items = [self.add_manual(trade_date, code, reason, priority, quant_run_id) for code in normalized]
        return {"items": items, "count": len(items)}

    def delete_manual(self, selection_id: int) -> None:
        row = self.session.get(ManualSelectionRecord, selection_id)
        if row is None:
            raise ValueError("MANUAL_SELECTION_NOT_FOUND")
        self.session.delete(row)
        self.session.commit()

    def clear_manual(self, trade_date: date) -> int:
        rows = list(self.session.scalars(select(ManualSelectionRecord).where(ManualSelectionRecord.trade_date == trade_date)))
        for row in rows:
            self.session.delete(row)
        self.session.commit()
        return len(rows)

    def start_job(self, job_type: str, trade_date: date, *, mode: str = "USE_EXISTING") -> dict[str, Any]:
        if job_type not in JOB_TYPES:
            raise ValueError("INVALID_JOB_TYPE")
        if mode not in {"USE_EXISTING", "MOCK"}:
            raise ValueError("REAL_WORKBENCH_JOBS_REQUIRE_EXPLICIT_BACKEND_ENABLEMENT")
        if mode == "USE_EXISTING":
            bundle = self.reconcile(trade_date)
            if bundle["source_mode"] == "EMPTY":
                raise ValueError("NO_COMPLETED_PIPELINE_RUN")
            return {"mode": "USE_EXISTING", "stage": "LOADED_EXISTING", "status": bundle["pipeline_status"], "bundle": bundle}
        active = self.session.scalar(select(PipelineJob).where(PipelineJob.job_type == job_type, PipelineJob.trade_date == trade_date, PipelineJob.status.in_(ACTIVE_JOB_STATUSES)))
        if active:
            raise ValueError(f"DUPLICATE_JOB:{active.job_id}")
        completed = self.session.scalar(select(PipelineJob).where(
            PipelineJob.job_type == job_type,
            PipelineJob.trade_date == trade_date,
            PipelineJob.status == "SUCCESS",
        ).order_by(PipelineJob.created_at.desc()))
        if completed and (completed.checkpoint or {}).get("mode") == mode:
            return _job_payload(completed)
        run_ids = self.status(trade_date)["latest_run_ids"]
        job = PipelineJob(job_id=f"workbench-{uuid.uuid4().hex[:20]}", job_type=job_type, trade_date=trade_date, status="SUCCESS", stage="MOCK_COMPLETED", progress_current=1, progress_total=1, success_count=1, started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc), run_ids=run_ids, output_path=None, checkpoint={"mode": mode})
        self.session.add(job)
        self.session.commit()
        return self.job(job.job_id)

    def job(self, job_id: str) -> dict[str, Any]:
        row = self.session.scalar(select(PipelineJob).where(PipelineJob.job_id == job_id))
        if row is None:
            raise ValueError("JOB_NOT_FOUND")
        return _job_payload(row)

    def jobs(self, trade_date: date) -> list[dict[str, Any]]:
        rows = list(self.session.scalars(select(PipelineJob).where(PipelineJob.trade_date == trade_date).order_by(PipelineJob.created_at.desc())))
        return [_job_payload(row) for row in rows]

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        row = self.session.scalar(select(PipelineJob).where(PipelineJob.job_id == job_id))
        if row is None:
            raise ValueError("JOB_NOT_FOUND")
        if row.status in ACTIVE_JOB_STATUSES:
            row.status, row.stage, row.finished_at = "CANCELLED", "CANCELLED", datetime.now(timezone.utc)
            self.session.commit()
        return _job_payload(row)

    def resume_job(self, job_id: str) -> dict[str, Any]:
        row = self.session.scalar(select(PipelineJob).where(PipelineJob.job_id == job_id))
        if row is None:
            raise ValueError("JOB_NOT_FOUND")
        if row.status not in {"FAILED", "CANCELLED"}:
            raise ValueError("JOB_NOT_RESUMABLE")
        resumed = PipelineJob(
            job_id=f"workbench-{uuid.uuid4().hex[:20]}", job_type=row.job_type, trade_date=row.trade_date,
            status="SUCCESS", stage="RESUMED_EXISTING", progress_current=row.progress_total or 1,
            progress_total=row.progress_total or 1, success_count=row.success_count, failure_count=row.failure_count,
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc), run_ids=row.run_ids or {},
            checkpoint={"mode": "USE_EXISTING", "resumed_from": row.job_id},
        )
        self.session.add(resumed)
        self.session.commit()
        return _job_payload(resumed)

    def _latest_quant(self, trade_date: date) -> QuantRun | None:
        return self.session.scalar(select(QuantRun).where(QuantRun.base_market_trade_date == trade_date).order_by(QuantRun.created_at.desc()))

    def _latest_flash(self, trade_date: date) -> ModelValidationRun | None:
        return self.session.scalar(select(ModelValidationRun).where(ModelValidationRun.base_market_trade_date == trade_date).order_by(ModelValidationRun.created_at.desc()))

    def _latest_pro(self, flash_run_id: str | None) -> ProResumeRun | None:
        if not flash_run_id:
            return None
        return self.session.scalar(select(ProResumeRun).where(ProResumeRun.flash_validation_run_id == flash_run_id).order_by(ProResumeRun.created_at.desc()))

    def _job_status(self, job_type: str, trade_date: date) -> dict[str, Any]:
        row = self.session.scalar(select(PipelineJob).where(
            PipelineJob.job_type == job_type, PipelineJob.trade_date == trade_date,
        ).order_by(PipelineJob.created_at.desc()))
        return self._run_status(row)

    def _existing_export_path(self, trade_date: date) -> str | None:
        output_dir = Path("outputs") / trade_date.isoformat()
        files = sorted(output_dir.glob("*.xlsx")) if output_dir.exists() else []
        return str(files[-1]) if files else None

    def _data_status(self, run: QuantRun | None) -> dict[str, Any]:
        return {"status": "READY" if run and run.temporal_status == "PASS" else "NOT_READY", "temporal_gate": run.temporal_status if run else "NOT_RUN", "coverage": 1.0 if run else 0.0}

    def _run_status(self, run: Any | None) -> dict[str, Any]:
        return {"status": getattr(run, "status", "NOT_RUN") if run else "NOT_RUN", "run_id": getattr(run, "run_id", None) if run else None, "updated_at": getattr(run, "updated_at", None) if run else None}

    def _manual_codes(self, trade_date: date) -> set[str]:
        return {row.stock_code for row in self.session.scalars(select(ManualSelectionRecord).where(ManualSelectionRecord.trade_date == trade_date))}

    def _bundle_manual_codes(self, bundle: dict[str, Any]) -> set[str]:
        pro_run_id = bundle.get("pro_run_id")
        if not pro_run_id:
            return set()
        pro = self.session.scalar(select(ProResumeRun).where(ProResumeRun.run_id == pro_run_id))
        sources = dict((pro.config_snapshot or {}).get("selection_sources") or {}) if pro else {}
        return {normalize_ts_code(code) for code, source in sources.items() if source in {"MANUAL", "BOTH"}}

    def _bundle_candidate_codes(self, bundle: dict[str, Any]) -> set[str]:
        pro_run_id = bundle.get("pro_run_id")
        if not pro_run_id:
            return set()
        pro = self.session.scalar(select(ProResumeRun).where(ProResumeRun.run_id == pro_run_id))
        return {normalize_ts_code(code) for code in ((pro.config_snapshot or {}).get("candidate_codes") or [])} if pro else set()

    def _stock_names(self, rows: list[Any]) -> dict[str, str]:
        codes = {normalize_ts_code(row.stock_code) for row in rows}
        if not codes:
            return {}
        masters = self.session.scalars(select(StockMaster).where(StockMaster.code.in_(codes))).all()
        return {normalize_ts_code(row.code): row.name for row in masters}

    def _setting(self, key: str, default: Any) -> Any:
        row = self.session.scalar(select(SystemConfig).where(SystemConfig.config_key == f"workbench.{key}"))
        return row.config_value if row else default


def _page(items: list[dict[str, Any]], total: int, page: int, page_size: int, **extra: Any) -> dict[str, Any]:
    start = (page - 1) * page_size
    total_pages = (total + page_size - 1) // page_size if total else 0
    effective_page = 1 if total == 0 else page
    return {"items": items[start:start + page_size], "total": total, "page": effective_page, "page_size": page_size, "total_pages": total_pages, **extra}


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _error_category(meta: dict[str, Any]) -> str | None:
    errors = meta.get("errors") or []
    return errors[0].get("error_category") if errors else None


def _field(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else value


def _fundamental_status(fundamental: dict[str, Any]) -> str | None:
    value = fundamental.get("financial_status")
    return value.get("status") if isinstance(value, dict) else value


def _financial_snapshot_summary(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return ""
    annual_revenue = snapshot.get("annual_revenue")
    q1_revenue = snapshot.get("q1_revenue")
    q1_deducted = snapshot.get("q1_deducted_net_profit")
    q1_non_recurring = snapshot.get("q1_non_recurring_gain")
    parts = []
    if annual_revenue is not None:
        parts.append(
            f"2025营收{float(annual_revenue) / 100_000_000:.2f}亿元"
            f"（同比{float(snapshot.get('annual_revenue_yoy_pct') or 0):+.2f}%）"
        )
    if q1_revenue is not None:
        parts.append(
            f"2026Q1营收{float(q1_revenue) / 100_000_000:.2f}亿元"
            f"（同比{float(snapshot.get('q1_revenue_yoy_pct') or 0):+.2f}%）"
        )
    if q1_deducted is not None:
        parts.append(
            f"扣非净利{float(q1_deducted) / 10_000:.2f}万元"
            f"（同比{float(snapshot.get('q1_deducted_net_profit_yoy_pct') or 0):+.2f}%）"
        )
    if q1_non_recurring is not None:
        parts.append(f"非经常性损益{float(q1_non_recurring) / 10_000:.2f}万元")
    return "；".join(parts)


def _concept_names(values: list[Any]) -> list[str]:
    result = []
    for value in values:
        name = (
            str(value.get("name") or value.get("value") or "").strip()
            if isinstance(value, dict)
            else str(value).strip()
        )
        if name:
            result.append(name)
    return result


def _status_label(value: Any) -> str:
    return {
        "SUCCESS": "已补全",
        "FAILED": "失败",
        "UNKNOWN": "信息不足",
        "EXTERNAL_VERIFIED": "外部资料已核验",
        "DEEPSEEK_UNVERIFIED": "模型推断未核验",
        "STRUCTURED_INPUT_ONLY": "结构化数据",
        "UPSTREAM": "上游",
        "MIDSTREAM": "中游",
        "DOWNSTREAM": "下游",
        "MULTI_SEGMENT": "多环节",
        "SERVICE_PLATFORM": "服务平台",
        "STABLE": "稳定",
        "PRESSURED": "承压",
        "INSUFFICIENT_DATA": "信息不足",
    }.get(str(value or "UNKNOWN"), str(value or "信息不足"))


def _server_mode() -> bool:
    return os.getenv("APP_RUNTIME_MODE", "").strip().upper() == "INTERNAL_WEB_SERVER"


def _priority(value: str) -> str:
    if value not in {"HIGH", "MEDIUM", "LOW"}:
        raise ValueError("INVALID_MANUAL_PRIORITY")
    return value


def _validate_settings(values: dict[str, Any]) -> None:
    integer_keys = {
        "quant_top_n", "llm_analysis_n", "llm_top_n", "final_display_n",
        "manual_soft_limit", "manual_max_limit", "daily_token_limit",
        "flash_concurrency", "flash_batch_size", "validation_account_equity",
        "validation_available_cash", "market_review_max_queries",
        "market_review_max_results_per_query", "market_review_max_age_hours",
        "market_review_multi_source_count", "market_review_pro_max_tokens",
    }
    if any(not isinstance(values[key], int) or values[key] <= 0 for key in integer_keys):
        raise ValueError("WORKBENCH_SETTINGS_REQUIRE_POSITIVE_INTEGERS")
    if values["llm_analysis_n"] > values["quant_top_n"] or values["llm_top_n"] > values["llm_analysis_n"]:
        raise ValueError("WORKBENCH_SELECTION_LIMITS_INVALID")
    if not 0 < float(values["token_warning_ratio"]) <= 1:
        raise ValueError("WORKBENCH_TOKEN_WARNING_RATIO_INVALID")
    if not 0 <= float(values["market_review_evidence_min_confidence"]) <= 1:
        raise ValueError("MARKET_REVIEW_CONFIDENCE_INVALID")
    if values["market_review_search_provider"] not in {"AUTO", "TAVILY", "MOCK", "DISABLED"}:
        raise ValueError("MARKET_REVIEW_SEARCH_PROVIDER_INVALID")


def _job_payload(row: PipelineJob) -> dict[str, Any]:
    return {key: getattr(row, key) for key in (
        "job_id", "job_type", "trade_date", "status", "stage", "progress_current", "progress_total", "current_stock", "success_count", "failure_count", "token_usage", "cost_usd", "started_at", "finished_at", "error_code", "error_message", "run_ids", "output_path", "checkpoint",
    )}
