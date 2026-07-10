from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config_manager import ConfigManager
from database.models.quant_run import QuantRun
from database.models.temporal import RunDataManifestRecord
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationLLMAudit,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
    ValidationAccountSnapshot,
)
from datasource.schemas import KlineBar
from fundamentals.pipeline import CachedTushareProfileService
from order_price.config import load_order_price_config
from order_price.order_plan_generator import generate_order_plan
from order_price.schemas import OrderPriceInput
from position_sizing.engine import PositionSizingEngine
from position_sizing.schemas import AccountState, SizingCandidate
from quant.run_repository import QuantRunRepository
from research.knowledge_mode import LLMKnowledgeMode, validate_knowledge_mode
from research.structured_validation import (
    FUNDAMENTAL_PROMPT_VERSION,
    MODEL_ALIAS,
    SCREENING_PROMPT_VERSION,
    StructuredValidationProvider,
)
from temporal.schemas import DatasetWatermark
from temporal.watermarks import DatasetWatermarkService


SH = ZoneInfo("Asia/Shanghai")
NON_ACTIONABLE_NOTICE = "仅供模型验证，不可作为正式交易仓位建议"


class GuardedValidationService:
    def __init__(self, session: Session, cache_root: Path | str = "data/cache/tushare") -> None:
        self.session = session
        self.cache_root = Path(cache_root)

    def preview(self, *, quant_run_id: str | None = None, ranks: tuple[int, ...] = (1, 250, 500)) -> dict[str, Any]:
        run, manifest, samples = self._load_context(quant_run_id, ranks=ranks)
        profiles = [self._profile(row.stock_code, run) for row in samples]
        gates = self.real_gate_failures(run, manifest, profiles)
        return {
            "status": "DRY_RUN", "model_calls": 0, "quant_run_id": run.run_id,
            "manifest_id": run.data_manifest_id, "run_mode": run.run_mode,
            "knowledge_mode": LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value,
            "selected_ranks": [row.rank for row in samples],
            "selected_stock_codes": [row.stock_code for row in samples],
            "profile_versions": [item.profile_version for item in profiles],
            "real_gate_ready": not gates, "missing_gates": gates,
        }

    def run_real(
        self, *, quant_run_id: str | None = None,
        ranks: tuple[int, ...] = (1, 250, 500),
        account_equity: Decimal = Decimal("1000000"),
        available_cash: Decimal = Decimal("1000000"),
    ) -> str:
        run, manifest, samples = self._load_context(quant_run_id, ranks=ranks)
        profiles = [self._profile(row.stock_code, run) for row in samples]
        failures = self.real_gate_failures(run, manifest, profiles)
        if failures:
            raise ValueError("REAL_LLM_GUARDS_NOT_SATISFIED:" + ",".join(failures))
        provider = StructuredValidationProvider()
        validation_run_id = f"validation-{uuid.uuid4().hex[:20]}"
        sample_payloads: list[dict[str, Any]] = []
        for rank_row, profile in zip(samples, profiles):
            context = self._structured_context(run, manifest, rank_row, profile)
            reused = self._find_reusable_sample(run, manifest, rank_row, profile)
            if reused:
                fundamental = reused.fundamental_result
                screening = reused.screening_result
                provider.audit.extend(self._reused_audit_rows(run, reused))
            else:
                fundamental = provider.fundamental(context, run_mode=run.run_mode, use_real_llm=True)
                fundamental = _normalize_fundamental(fundamental, profile)
                screening_context = {
                    "stock_code": rank_row.stock_code,
                    "quant": context["quant"], "fundamental_inference": fundamental,
                    "financial_status": profile.financial_status,
                    "missing_fields": profile.missing_fields,
                    "provenance": context["provenance"],
                }
                screening = provider.screening(screening_context, run_mode=run.run_mode, use_real_llm=True)
            sample_payloads.append({
                "rank_row": rank_row, "profile": profile, "fundamental": fundamental,
                "screening": screening, "context": context,
            })
        fresh_call_count = sum(1 for row in provider.audit if row.get("cache_status") != "REUSED")
        if fresh_call_count > 2 * len(samples):
            raise ValueError("MODEL_VALIDATION_LLM_CALL_LIMIT_EXCEEDED")
        plans = [self._build_order_plan(run, item) for item in sample_payloads]
        snapshot_id = f"validation-account-{uuid.uuid4().hex[:16]}"
        allocation_run_id = f"validation-allocation-{uuid.uuid4().hex[:16]}"
        allocations = self._size_positions(sample_payloads, plans, account_equity, available_cash)
        request_hash = hashlib.sha256(
            json.dumps({"validation_run_id": validation_run_id, "quant_run_id": run.run_id, "manifest": run.data_manifest_id, "profiles": [p.profile_version for p in profiles]}, sort_keys=True).encode()
        ).hexdigest()
        universe_audit = self._expected_universe_audit(run.base_market_trade_date)
        row = ModelValidationRun(
            run_id=validation_run_id, quant_run_id=run.run_id, run_data_manifest_id=run.data_manifest_id,
            run_mode=run.run_mode, knowledge_mode=LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value,
            decision_time=run.decision_time, base_market_trade_date=run.base_market_trade_date,
            target_trade_date=run.target_trade_date, real_llm=True, status="COMPLETED",
            request_hash=request_hash, config_snapshot={"order_price": load_order_price_config().summary(), "position_sizing": "position-sizing-v1"},
            expected_universe_audit=universe_audit, warnings=[NON_ACTIONABLE_NOTICE],
        )
        self.session.add(row)
        for item in sample_payloads:
            rank_row, profile = item["rank_row"], item["profile"]
            self.session.add(ModelValidationSample(
                validation_run_id=validation_run_id, quant_run_id=run.run_id,
                run_data_manifest_id=run.data_manifest_id, rank=rank_row.rank,
                stock_code=rank_row.stock_code, stock_name=profile.stock_name or rank_row.stock_code,
                quant_scores=_quant_scores(rank_row), profile_version=profile.profile_version,
                latest_financial_period=profile.latest_financial_period,
                financial_available_at=profile.available_at, data_age_days=profile.financial_data_age_days,
                selected_at=datetime.now(timezone.utc), fundamental_result=item["fundamental"],
                screening_result=item["screening"], field_provenance=item["context"]["provenance"],
                missing_fields=profile.missing_fields,
            ))
        for audit in provider.audit:
            self.session.add(ModelValidationLLMAudit(validation_run_id=validation_run_id, **{
                key: audit.get(key) for key in (
                    "stock_code", "task", "knowledge_mode", "model_alias", "actual_model", "prompt_version",
                    "status", "schema_status", "request_hash", "input_tokens", "output_tokens", "cost_usd",
                    "latency_ms", "cache_status", "error_category", "error_field", "error_message",
                    "diagnostics",
                )
            }))
        for plan in plans:
            self.session.add(ModelValidationOrderPlan(validation_run_id=validation_run_id, **plan))
        self.session.add(ValidationAccountSnapshot(
            snapshot_id=snapshot_id, validation_run_id=validation_run_id, account_equity=account_equity,
            available_cash=available_cash, snapshot_time=run.decision_time, existing_positions=[],
        ))
        for item in allocations:
            self.session.add(ModelValidationAllocation(
                validation_run_id=validation_run_id, allocation_run_id=allocation_run_id,
                account_snapshot_id=snapshot_id, **item,
            ))
        self.session.commit()
        return validation_run_id

    def real_gate_failures(self, run: QuantRun, manifest: RunDataManifestRecord, profiles: list[Any]) -> list[str]:
        failures = []
        if not os.getenv("DEEPSEEK_API_KEY", "").strip(): failures.append("DEEPSEEK_API_KEY_NOT_CONFIGURED")
        if not _flag("LLM_REAL_CALLS_ENABLED"): failures.append("LLM_REAL_CALLS_ENABLED_FALSE")
        if not _flag("RUN_REAL_FUNDAMENTAL_RESEARCH"): failures.append("RUN_REAL_FUNDAMENTAL_RESEARCH_FALSE")
        if ConfigManager().get_llm_gateway_config().get("llm", {}).get("mock_only", True): failures.append("GATEWAY_MOCK_ONLY")
        if run.status != "COMPLETED" or not run.no_llm_call_verified: failures.append("FORMAL_QUANT_RUN_REQUIRED")
        if run.temporal_status not in {"PASS", "PASS_WITH_WARNINGS"} or not run.actionable: failures.append("TEMPORAL_GATE_NOT_PASS")
        if manifest is None or manifest.temporal_status not in {"PASS", "PASS_WITH_WARNINGS"} or not manifest.actionable: failures.append("MANIFEST_GATE_NOT_PASS")
        decision = _aware_shanghai(run.decision_time)
        for profile in profiles:
            if profile.available_at is None or _aware_shanghai(profile.available_at) > decision:
                failures.append(f"PROFILE_NOT_POINT_IN_TIME:{profile.stock_code}")
        return failures

    def readback(self, validation_run_id: str) -> dict[str, Any]:
        run = self.session.scalar(select(ModelValidationRun).where(ModelValidationRun.run_id == validation_run_id))
        if run is None: raise ValueError("VALIDATION_RUN_NOT_FOUND")
        samples = list(self.session.scalars(select(ModelValidationSample).where(ModelValidationSample.validation_run_id == validation_run_id).order_by(ModelValidationSample.rank)))
        audits = list(self.session.scalars(select(ModelValidationLLMAudit).where(ModelValidationLLMAudit.validation_run_id == validation_run_id).order_by(ModelValidationLLMAudit.id)))
        plans = list(self.session.scalars(select(ModelValidationOrderPlan).where(ModelValidationOrderPlan.validation_run_id == validation_run_id).order_by(ModelValidationOrderPlan.id)))
        snapshots = list(self.session.scalars(select(ValidationAccountSnapshot).where(ValidationAccountSnapshot.validation_run_id == validation_run_id)))
        allocations = list(self.session.scalars(select(ModelValidationAllocation).where(ModelValidationAllocation.validation_run_id == validation_run_id).order_by(ModelValidationAllocation.id)))
        return {"run": run, "samples": samples, "audits": audits, "plans": plans, "snapshots": snapshots, "allocations": allocations}

    def _load_context(self, quant_run_id: str | None, *, ranks: tuple[int, ...] = (1, 250, 500)):
        repo = QuantRunRepository(self.session)
        run = self.session.scalar(select(QuantRun).where(QuantRun.run_id == quant_run_id)) if quant_run_id else repo.latest_actionable()
        if run is None: raise ValueError("ACTIONABLE_QUANT_RUN_REQUIRED")
        validate_knowledge_mode(run.run_mode, LLMKnowledgeMode.STRUCTURED_INPUT_ONLY)
        manifest = self.session.scalar(select(RunDataManifestRecord).where(RunDataManifestRecord.manifest_id == run.data_manifest_id))
        if manifest is None: raise ValueError("RUN_DATA_MANIFEST_REQUIRED")
        if tuple(ranks) == (1, 250, 500):
            samples = repo.samples(run.run_id, 3)
            if [row.rank for row in samples] != [1, 250, 500] and run.top_count == 500:
                raise ValueError("DETERMINISTIC_SAMPLE_RANK_MISMATCH")
            if len(samples) != 3: raise ValueError("THREE_QUANT_SAMPLES_REQUIRED")
            return run, manifest, samples
        if tuple(ranks) != (1,):
            raise ValueError("VALIDATION_RANKS_MUST_BE_1_OR_1_250_500")
        rows = repo.samples(run.run_id, 3)
        samples = [row for row in rows if row.rank == 1]
        if len(samples) != 1 or samples[0].stock_code != "000518":
            raise ValueError("RANK1_CANARY_SAMPLE_MISMATCH")
        return run, manifest, samples

    def _find_reusable_sample(self, run, manifest, rank_row, profile) -> ModelValidationSample | None:
        sample = self.session.scalar(
            select(ModelValidationSample)
            .join(ModelValidationRun, ModelValidationRun.run_id == ModelValidationSample.validation_run_id)
            .where(
                ModelValidationRun.real_llm.is_(True),
                ModelValidationRun.status.in_(["COMPLETED", "SUCCESS", "PARTIAL_SUCCESS"]),
                ModelValidationSample.quant_run_id == run.run_id,
                ModelValidationSample.run_data_manifest_id == manifest.manifest_id,
                ModelValidationSample.stock_code == rank_row.stock_code,
                ModelValidationSample.rank == rank_row.rank,
                ModelValidationSample.profile_version == profile.profile_version,
            )
            .order_by(ModelValidationSample.id.desc())
        )
        if sample is None:
            return None
        audits = list(self.session.scalars(select(ModelValidationLLMAudit).where(ModelValidationLLMAudit.validation_run_id == sample.validation_run_id, ModelValidationLLMAudit.stock_code == rank_row.stock_code)))
        required = {
            ("fundamental_structured_inference", FUNDAMENTAL_PROMPT_VERSION),
            ("structured_light_screening", SCREENING_PROMPT_VERSION),
        }
        passed = {(row.task, row.prompt_version) for row in audits if row.status in {"ok", "SUCCESS"} and row.schema_status == "PASS"}
        return sample if required.issubset(passed) else None

    @staticmethod
    def _reused_audit_rows(run, sample: ModelValidationSample) -> list[dict[str, Any]]:
        rows = []
        for task, prompt_version in (
            ("fundamental_structured_inference", FUNDAMENTAL_PROMPT_VERSION),
            ("structured_light_screening", SCREENING_PROMPT_VERSION),
        ):
            rows.append({
                "stock_code": sample.stock_code,
                "task": task,
                "knowledge_mode": LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value,
                "model_alias": MODEL_ALIAS,
                "actual_model": "deepseek-v4-flash",
                "prompt_version": prompt_version,
                "status": "ok",
                "schema_status": "PASS",
                "request_hash": hashlib.sha256(f"{run.run_id}:{sample.stock_code}:{task}:{sample.profile_version}:reused".encode()).hexdigest(),
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": Decimal("0"),
                "latency_ms": 0,
                "cache_status": "REUSED",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        return rows

    @staticmethod
    def _profile(stock_code: str, run: QuantRun):
        profile = CachedTushareProfileService().build(stock_code, decision_time=_aware_shanghai(run.decision_time))
        if profile.available_at is None: raise ValueError(f"FRESH_FUNDAMENTAL_PROFILE_REQUIRED:{stock_code}")
        return profile

    @staticmethod
    def _structured_context(run, manifest, rank_row, profile) -> dict[str, Any]:
        return {
            "stock_code": rank_row.stock_code, "stock_name": profile.stock_name,
            "company_profile": profile.company_profile, "main_business": profile.main_business,
            "main_business_breakdown": profile.main_business_breakdown,
            "level_one_sector": profile.level_one_sector, "concept_tags": profile.normalized_concept_tags or profile.source_concept_tags,
            "concept_source_status": profile.concept_source_status,
            "concept_mapping_audit": profile.concept_mapping_audit,
            "financial_summary": profile.financial_summary, "financial_status": profile.financial_status,
            "missing_fields": profile.missing_fields,
            "quant": {"rank": rank_row.rank, **_quant_scores(rank_row)},
            "provenance": {key: value.model_dump(mode="json") for key, value in profile.field_provenance_map.items()},
            "manifest": {"id": manifest.manifest_id, "decision_time": str(manifest.decision_time), "base_trade_date": str(manifest.base_market_trade_date), "target_trade_date": str(manifest.target_trade_date)},
        }

    def _build_order_plan(self, run: QuantRun, item: dict[str, Any]) -> dict[str, Any]:
        code, profile, rank_row = item["rank_row"].stock_code, item["profile"], item["rank_row"]
        bars = self._load_bars(code, run.base_market_trade_date)
        if len(bars) < 15: raise ValueError(f"INSUFFICIENT_POINT_IN_TIME_KLINE:{code}")
        basic = self._stock_basic(code)
        previous_close = bars[-1].close
        up, down, rule_warning = _estimated_limits(code, profile.stock_name or "", basic, previous_close, run.target_trade_date)
        config = load_order_price_config()
        screening = item["screening"]
        recommendation = "BLOCKED" if screening["screening_decision"] == "REJECT" else "WATCH"
        risk_level = "HIGH" if str(profile.financial_status.get("status")) in {"HIGH_RISK", "BLOCKED"} else "NORMAL"
        latest = bars[-1]
        context = OrderPriceInput(
            stock_code=code, stock_name=profile.stock_name or code, industry=profile.level_one_sector,
            committee_score=Decimal(str(rank_row.total_score)), recommendation=recommendation,
            risk_level=risk_level, confidence=Decimal(str(screening["confidence"])),
            previous_close=previous_close, latest_price=latest.close, limit_up_price=up, limit_down_price=down,
            kline_bars=bars, volume=latest.volume, amount=latest.amount,
            capital_score=Decimal(str(rank_row.capital_score)), emotion_score=Decimal(str(rank_row.emotion_score)),
        )
        draft = generate_order_plan(context, config, plan_date=run.target_trade_date)
        candidate_map = {candidate.price_type.lower(): candidate for candidate in draft.candidates}
        recommended = next((candidate for candidate in draft.candidates if candidate.price == draft.recommended_price), None)
        valid = draft.valid_conditions
        warnings = [NON_ACTIONABLE_NOTICE, "TARGET_DAY_LIMIT_RULE_ESTIMATED", "LLM_UNVERIFIED_POSITION_DISCOUNT_APPLIED"]
        if rule_warning: warnings.append(rule_warning)
        status = "DRAFT" if draft.status == "DRAFT" and not rule_warning else ("NEEDS_REVIEW" if rule_warning else draft.status)
        return {
            "quant_run_id": run.run_id, "run_data_manifest_id": run.data_manifest_id, "stock_code": code,
            "status": status, "decision_time": run.decision_time, "base_market_trade_date": run.base_market_trade_date,
            "target_trade_date": run.target_trade_date, "factor_version": run.factor_version,
            "config_snapshot": config.summary(),
            "conservative_price": getattr(candidate_map.get("conservative"), "price", None),
            "balanced_price": getattr(candidate_map.get("balanced"), "price", None),
            "aggressive_price": getattr(candidate_map.get("aggressive"), "price", None),
            "recommended_price": draft.recommended_price, "max_acceptable_price": draft.max_acceptable_price,
            "stop_loss_price": draft.stop_loss_price, "take_profit_1_price": draft.take_profit_1_price,
            "take_profit_2_price": draft.take_profit_2_price,
            "fill_probability": getattr(recommended, "fill_probability", None),
            "risk_reward": draft.active_risk_reward,
            "risk_reward_to_tp1": draft.risk_reward_to_tp1,
            "risk_reward_to_tp2": draft.risk_reward_to_tp2,
            "active_risk_reward": draft.active_risk_reward,
            "active_target_mode": draft.active_target_mode,
            "unrounded_stop_loss_price": draft.unrounded_stop_loss_price,
            "order_price_score": getattr(recommended, "score", None),
            "support": _decimal_or_none(valid.get("support")), "resistance": _decimal_or_none(valid.get("resistance")),
            "atr": _decimal_or_none(valid.get("atr")), "vwap": _decimal_or_none(valid.get("vwap")),
            "previous_close": previous_close, "limit_up_estimated": up, "limit_down_estimated": down,
            "cancel_conditions": draft.cancel_conditions, "reprice_conditions": draft.reprice_conditions,
            "warnings": warnings, "temporal_status": run.temporal_status,
        }

    def _size_positions(
        self, samples: list[dict[str, Any]], plans: list[dict[str, Any]],
        account_equity: Decimal, available_cash: Decimal,
    ) -> list[dict[str, Any]]:
        candidates = []
        for item, plan in zip(samples, plans):
            rank_row, profile = item["rank_row"], item["profile"]
            candidates.append(SizingCandidate(
                stock_code=rank_row.stock_code, final_score=Decimal(str(rank_row.total_score)),
                controller_confidence=Decimal(str(item["screening"]["confidence"])), data_quality_factor=Decimal("0.8"),
                entry_price=plan["recommended_price"], stop_price=plan["stop_loss_price"],
                unrounded_stop_price=plan.get("unrounded_stop_loss_price"),
                max_acceptable_price=plan["max_acceptable_price"], risk_reward=plan["risk_reward"], atr=plan["atr"],
                average_daily_amount=sum((bar.amount for bar in self._load_bars(rank_row.stock_code, plan["base_market_trade_date"])), Decimal("0")) / Decimal("20"),
                industry=profile.level_one_sector or "UNKNOWN", industry_chain=str(item["fundamental"].get("industry_chain", {}).get("chain_name") or "UNKNOWN"),
                risk_level="HIGH" if plan["status"] in {"BLOCKED", "NEEDS_REVIEW"} else "NORMAL",
                blocked=plan["status"] == "BLOCKED", data_conflict=bool(item["screening"].get("data_conflict")),
                unverified_fundamental_research=True,
                tick_size=load_order_price_config().tick_size,
                stop_validation_tolerance_ticks=load_order_price_config().stop_validation_tolerance_ticks,
            ))
        result = PositionSizingEngine().evaluate(AccountState(equity=account_equity, available_cash=available_cash), candidates)
        return [{
            "stock_code": suggestion.stock_code, "relative_allocation_weight": suggestion.relative_allocation_weight,
            "suggested_position_percent": suggestion.account_position_percent,
            "suggested_capital_amount": suggestion.suggested_capital, "suggested_quantity": suggestion.suggested_quantity,
            "estimated_max_loss": suggestion.maximum_planned_loss,
            "binding_constraints": [suggestion.binding_constraint],
            "warnings": sorted(set(suggestion.warnings + [NON_ACTIONABLE_NOTICE])),
        } for suggestion in result.suggestions]

    def _load_bars(self, stock_code: str, cutoff: date) -> list[KlineBar]:
        ts_code = _ts_code(stock_code)
        rows = []
        root = self.cache_root / "trade_date" / "daily"
        for path in sorted(root.glob("*.json"), reverse=True):
            if len(rows) >= 20: break
            if path.stem.isdigit() and datetime.strptime(path.stem, "%Y%m%d").date() <= cutoff:
                record = next((row for row in _read_records(path) if row.get("ts_code") == ts_code), None)
                if record: rows.append(record)
        rows.reverse()
        return [KlineBar(
            stock_code=stock_code, trade_date=datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(),
            open=Decimal(str(row["open"])), high=Decimal(str(row["high"])), low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])), pre_close=Decimal(str(row["pre_close"])),
            volume=int(Decimal(str(row.get("vol") or 0)) * 100),
            amount=Decimal(str(row.get("amount") or 0)) * 1000,
        ) for row in rows]

    def _stock_basic(self, stock_code: str) -> dict[str, Any]:
        root = self.cache_root / "fundamental" / "stock_basic"
        files = sorted(root.rglob("*.json"), reverse=True)
        code = _ts_code(stock_code)
        for path in files:
            row = next((item for item in _read_records(path) if item.get("ts_code") == code), None)
            if row: return row
        return {}

    def _expected_universe_audit(self, trade_date: date) -> dict[str, Any]:
        service = DatasetWatermarkService(self.cache_root)
        return {name: service.trade_date_watermark(name, trade_date).model_dump(mode="json") for name in ("daily", "daily_basic", "moneyflow", "stk_limit", "adj_factor")}


def _normalize_fundamental(payload: dict[str, Any], profile) -> dict[str, Any]:
    result = dict(payload)
    if not result.get("core_products"):
        result["core_products"] = ["信息不足*"]
    if not result.get("invalidation_conditions"):
        result["invalidation_conditions"] = ["信息不足，需人工复核*"]
    main = dict(result.get("main_business_summary") or {})
    main["derivation_status"] = "LLM_SUMMARY"
    if result.get("wire_schema_version") != "fundamental_enrichment_wire_v4":
        main.update({"source_status": "VERIFIED_STRUCTURED", "display_marker": ""})
    result["main_business_summary"] = main
    for key in ("industry_chain", "level_one_sector_explanation", "industry_position", "competitive_advantage", "industry_trend", "investment_logic", "domestic_substitution", "observation_rating"):
        value = result.get(key)
        if isinstance(value, dict): value.setdefault("display_marker", "*")
    result.setdefault("structural_theme_fit", {
        "value": "UNKNOWN" if not profile.main_business else "STRUCTURAL_FIT_REQUIRES_MANUAL_REVIEW",
        "source_status": "LLM_UNVERIFIED", "display_marker": "*",
    })
    return result


def _estimated_limits(code: str, name: str, basic: dict[str, Any], previous_close: Decimal, target_date: date) -> tuple[Decimal, Decimal, str | None]:
    warning = None
    list_status = str(basic.get("list_status") or "L").upper()
    if list_status not in {"L", "LISTED", "NORMAL"}: warning = "LISTING_STATUS_RULE_UNCERTAIN"
    list_date = str(basic.get("list_date") or "")
    if len(list_date) == 8 and list_date.isdigit():
        if (target_date - datetime.strptime(list_date, "%Y%m%d").date()).days <= 7:
            warning = "NEW_LISTING_LIMIT_RULE_REQUIRES_REVIEW"
    if "ST" in name.upper(): ratio = Decimal("0.05")
    elif code.startswith(("688", "300", "301")): ratio = Decimal("0.20")
    elif code.startswith(("4", "8", "920")): ratio = Decimal("0.30")
    else: ratio = Decimal("0.10")
    tick = Decimal("0.01")
    up = (previous_close * (Decimal("1") + ratio) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
    down = (previous_close * (Decimal("1") - ratio) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
    return up, down, warning


def _quant_scores(row) -> dict[str, Any]:
    return {key: str(getattr(row, key)) for key in ("total_score", "technical_score", "capital_score", "emotion_score", "momentum_score", "risk_score")}


def _read_records(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else list(value.get("records", []))


def _ts_code(code: str) -> str:
    if "." in code: return code.upper()
    if code.startswith(("4", "8", "920")): return f"{code}.BJ"
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


def _aware_shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SH) if value.tzinfo is None else value.astimezone(SH)


def _decimal_or_none(value) -> Decimal | None:
    return Decimal(str(value)) if value not in (None, "") else None


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}
