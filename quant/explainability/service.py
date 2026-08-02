from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from database.models.decision_explainability import (
    AdmissionV3Result,
    AdmissionV3Run,
    FactorAttribution,
    GateEvaluation,
    StrategyTimingContract,
)
from database.models.entry_timing_v2 import AdmissionV2Run, EntryTimingV2Result
from database.models.performance import SelectionCohort
from database.models.quant_run import QuantRankResult, QuantRun
from database.models.stock import StockMaster
from entry_timing.service_v2 import EntryTimingV2ShadowService
from entry_timing.strategy import StrategyFeatures
from quant.config import load_quant_config
from quant.explainability.admission_v3 import (
    ADMISSION_V3_VERSION,
    AdmissionV3Engine,
    AdmissionV3Input,
    calculate_shadow_ev,
)
from quant.explainability.factor_attribution import FactorAttributionEngine
from quant.explainability.factor_registry import RISK_LIQUIDITY, SENTIMENT_REGIME
from quant.explainability.gate_evaluation import GATE_EVALUATION_VERSION
from quant.explainability.strategy_probability import StrategyProbabilityClassifier
from quant.explainability.timing_contract import StrategyTimingContractSpec, validate_timing_contract
from stock_codes import normalize_ts_code


SHANGHAI = ZoneInfo("Asia/Shanghai")
SHADOW_CONFIG = {
    "phase": "Admission V3 Explainability Enhancement — Factor Performance + Gate Evaluation",
    "baseline": "TUSHARE_BASELINE_V1",
    "quant_weights": {"technical": 0.25, "capital": 0.25, "emotion": 0.20, "momentum": 0.15, "risk": 0.15},
    "entry_timing": "V2.2 Shadow",
    "admission_source": "V2.2 Shadow",
    "admission_target": ADMISSION_V3_VERSION,
    "shadow_only": True,
    "production_enabled": False,
    "llm_calls_enabled": False,
    "external_calls_enabled": False,
    "orders_enabled": False,
    "implementation_revision": "decision_explainability_v3_enhancement_r2_actual_timing",
}


class DecisionExplainabilityShadowService:
    def __init__(self, session) -> None:
        self.session = session
        self.probability = StrategyProbabilityClassifier()
        self.admission = AdmissionV3Engine()
        self.attribution = FactorAttributionEngine()

    def run(
        self,
        trade_date: date,
        *,
        source_v2_run_id: str | None = None,
        force_shadow: bool = False,
        available_at_ts: datetime | None = None,
    ) -> dict[str, Any]:
        if not force_shadow:
            raise ValueError("ADMISSION_V3_SHADOW_CONFIRMATION_REQUIRED")
        self._assert_frozen_quant_baseline()
        source_run = self._source_run(trade_date, source_v2_run_id)
        source_rows = _unique_source_rows(list(
            self.session.scalars(
                select(EntryTimingV2Result)
                .where(EntryTimingV2Result.run_id == source_run.run_id)
                .order_by(EntryTimingV2Result.quant_rank, EntryTimingV2Result.stock_code)
            )
        ))
        if not source_rows:
            raise ValueError("ADMISSION_V3_SOURCE_RESULTS_NOT_FOUND")
        quant = self.session.scalar(select(QuantRun).where(QuantRun.run_id == source_run.quant_run_id))
        if quant is None:
            raise ValueError("ADMISSION_V3_QUANT_RUN_NOT_FOUND")
        observation_end_ts = datetime.combine(trade_date, time(15, 0), SHANGHAI)
        effective_available_at = available_at_ts or datetime.now(SHANGHAI)
        if effective_available_at.tzinfo is None:
            effective_available_at = effective_available_at.replace(tzinfo=SHANGHAI)
        if effective_available_at < observation_end_ts:
            raise ValueError("INVALID_TIMING_CONTRACT")
        quant_rows = list(
            self.session.scalars(
                select(QuantRankResult)
                .where(QuantRankResult.quant_run_id == quant.run_id)
                .order_by(QuantRankResult.rank)
            )
        )
        quant_by_code = {normalize_ts_code(row.stock_code): row for row in quant_rows}
        stock_by_code = {
            normalize_ts_code(row.code): row
            for row in self.session.scalars(
                select(StockMaster).where(
                    StockMaster.code.in_({normalize_ts_code(item.stock_code) for item in source_rows})
                )
            )
        }
        source_hashes_before = self._source_hashes(source_run, quant, quant_rows)
        input_hash = _hash(
            {
                "trade_date": trade_date,
                "source_v2_run_id": source_run.run_id,
                "source_rows": [self._source_row_hash(row) for row in source_rows],
                "source_hashes": source_hashes_before,
                "config": SHADOW_CONFIG,
                "available_at_ts": effective_available_at.isoformat(),
            }
        )
        existing = self.session.scalar(select(AdmissionV3Run).where(AdmissionV3Run.input_hash == input_hash))
        if existing is not None:
            return self.summary(existing.run_id)

        run_id = f"admission-v3-shadow-{uuid.uuid4().hex[:16]}"
        industry_counts = Counter(
            (getattr(stock_by_code.get(normalize_ts_code(row.stock_code)), "industry", None) or "UNKNOWN")
            for row in source_rows
        )
        result_rows: list[AdmissionV3Result] = []
        factor_rows: list[FactorAttribution] = []
        decisions: dict[str, Any] = {}
        for source in source_rows:
            code = normalize_ts_code(source.stock_code)
            stock = stock_by_code.get(code)
            probability = self.probability.classify(self._strategy_features(source))
            industry = getattr(stock, "industry", None) or "UNKNOWN"
            risk_flags = tuple(source.risk_flags_json or [])
            components = source.component_scores_json or {}
            quant_row = quant_by_code.get(code)
            admission_input = AdmissionV3Input(
                quant_score=_number(source.quant_score),
                entry_timing_score=_number(source.entry_timing_v2_score),
                sector_strength=_number(components.get("sector_resonance")),
                momentum_score=_number(getattr(quant_row, "momentum_score", None)),
                strategy_probability=probability.strategy_probability,
                strategy_status=probability.classification_status,
                market_emotion_state=source.market_emotion_state,
                risk_flags=risk_flags,
                future_data_detected=bool(set(risk_flags) & {"FUTURE_DATA", "FUTURE_DATA_LEAKAGE"}),
                suspended=bool(set(risk_flags) & {"SUSPENDED", "SUSPENSION"}),
                is_st=bool(set(risk_flags) & {"ST", "ST_STOCK"}) or _is_st_name(source.stock_name),
                tradable=not bool(set(risk_flags) & {"UNTRADEABLE", "CANNOT_TRADE"}),
                black_swan=bool(set(risk_flags) & {"BLACK_SWAN", "MATERIAL_BLACK_SWAN"}),
                industry_concentration=industry_counts[industry] / len(source_rows),
                maximum_correlation=None,
                capacity_ratio=0.60 if set(risk_flags) & {"LIQUIDITY_RISK", "LOW_LIQUIDITY"} else 1.0,
            )
            decision = self.admission.decide(admission_input)
            shadow_ev = calculate_shadow_ev(admission_input, decision)
            decisions[code] = decision
            gate_contributions = _factor_gate_contributions(decision)
            attributions = self.attribution.attribute(
                stock_code=code,
                trade_date=trade_date,
                technical_score=_number(getattr(quant_row, "technical_score", None)),
                capital_score=_number(getattr(quant_row, "capital_score", None)),
                emotion_score=_number(getattr(quant_row, "emotion_score", None)),
                momentum_score=_number(getattr(quant_row, "momentum_score", None)),
                risk_score=_number(getattr(quant_row, "risk_score", None)),
                flash_score=_optional_number(source.flash_score),
                raw_metrics=_raw_metrics(source),
                gate_contributions=gate_contributions,
            )
            signal_generated_at = datetime.now(SHANGHAI)
            eligible_date = quant.target_trade_date
            if datetime.combine(eligible_date, time(9, 30), SHANGHAI) <= signal_generated_at:
                eligible_date = _next_weekday(signal_generated_at.date())
            contract = self._contract(
                source,
                quant,
                eligible_date,
                available_at_ts=effective_available_at,
                signal_generated_at=signal_generated_at,
            )
            largest_factor = max(attributions, key=lambda item: item.rank_contribution).factor_family
            for item in attributions:
                factor_rows.append(
                    FactorAttribution(
                        run_id=run_id,
                        timing_contract_id=contract.id,
                        stock_code=code,
                        trade_date=trade_date,
                        factor_family=item.factor_family,
                        raw_signal=item.raw_signal,
                        normalized_score=item.normalized_score,
                        score_contribution=item.score_contribution,
                        gate_contribution=item.gate_contribution,
                        rank_contribution=item.rank_contribution,
                        interaction_note=item.interaction_note,
                        lineage_json=item.lineage,
                        version=item.version,
                    )
                )
            result_rows.append(
                AdmissionV3Result(
                    run_id=run_id,
                    timing_contract_id=contract.id,
                    source_v2_run_id=source_run.run_id,
                    stock_code=code,
                    stock_name=source.stock_name,
                    trade_date=trade_date,
                    quant_rank=source.quant_rank,
                    industry=industry,
                    admission_state=decision.admission_state,
                    strategy_status=probability.classification_status,
                    strategy_probability=probability.strategy_probability,
                    hard_gate_results=decision.hard_gate_results,
                    risk_penalties=decision.risk_penalties,
                    opportunity_components=decision.opportunity_components,
                    portfolio_adjustments=decision.portfolio_adjustments,
                    counterfactuals=decision.counterfactuals,
                    base_score=decision.base_score,
                    opportunity_score=decision.opportunity_score,
                    final_score=decision.final_score,
                    expected_value_score=shadow_ev.expected_value_score,
                    risk_adjusted_opportunity_score=shadow_ev.risk_adjusted_opportunity_score,
                    position_multiplier=decision.position_multiplier,
                    selected_reason=decision.selected_reason,
                    rejected_reasons=decision.rejected_reasons,
                    largest_factor=largest_factor,
                    largest_gate=decision.largest_gate,
                    llm_structured_output={},
                    final_llm_score=None,
                    shadow_only=True,
                    version=decision.version,
                )
            )

        counts = Counter(row.admission_state for row in result_rows)
        gate_rows = self._gate_rows(run_id, trade_date, decisions)
        source_hashes_after = self._source_hashes(source_run, quant, quant_rows)
        if source_hashes_before != source_hashes_after:
            self.session.rollback()
            raise RuntimeError("ADMISSION_V3_SOURCE_HASH_CHANGED")
        run = AdmissionV3Run(
            run_id=run_id,
            trade_date=trade_date,
            source_v2_run_id=source_run.run_id,
            quant_run_id=quant.run_id,
            input_hash=input_hash,
            candidate_count=len(result_rows),
            pass_core_count=counts["PASS_CORE"],
            pass_exploratory_count=counts["PASS_EXPLORATORY"],
            review_count=counts["REVIEW"],
            reject_count=counts["REJECT"],
            open_set_count=sum(row.strategy_status == "OPEN_SET" for row in result_rows),
            config_snapshot=SHADOW_CONFIG,
            status="SUCCESS",
            shadow_only=True,
            enabled_in_production=False,
            quant_hash_before=source_hashes_before["quant"],
            quant_hash_after=source_hashes_after["quant"],
            flash_hash_before=source_hashes_before["flash"],
            flash_hash_after=source_hashes_after["flash"],
            pro_hash_before=source_hashes_before["pro"],
            pro_hash_after=source_hashes_after["pro"],
            llm_call_count=0,
            external_api_call_count=0,
            order_creation_count=0,
            version=ADMISSION_V3_VERSION,
        )
        self.session.add(run)
        self.session.add_all(factor_rows)
        self.session.add_all(result_rows)
        self.session.add_all(gate_rows)
        self.session.commit()
        return self.summary(run_id)

    def latest(self, trade_date: date) -> dict[str, Any] | None:
        run = self.session.scalar(
            select(AdmissionV3Run)
            .where(AdmissionV3Run.trade_date == trade_date)
            .order_by(AdmissionV3Run.created_at.desc())
        )
        return self.summary(run.run_id) if run else None

    def summary(self, run_id: str) -> dict[str, Any]:
        run = self.session.scalar(select(AdmissionV3Run).where(AdmissionV3Run.run_id == run_id))
        if run is None:
            raise ValueError("ADMISSION_V3_RUN_NOT_FOUND")
        return {
            "run_id": run.run_id,
            "trade_date": run.trade_date.isoformat(),
            "source_v2_run_id": run.source_v2_run_id,
            "quant_run_id": run.quant_run_id,
            "candidate_count": run.candidate_count,
            "pass_core_count": run.pass_core_count,
            "pass_exploratory_count": run.pass_exploratory_count,
            "review_count": run.review_count,
            "reject_count": run.reject_count,
            "open_set_count": run.open_set_count,
            "status": run.status,
            "shadow_only": run.shadow_only,
            "enabled_in_production": run.enabled_in_production,
            "quant_hash_unchanged": run.quant_hash_before == run.quant_hash_after,
            "flash_hash_unchanged": run.flash_hash_before == run.flash_hash_after,
            "pro_hash_unchanged": run.pro_hash_before == run.pro_hash_after,
            "llm_calls": run.llm_call_count,
            "external_api_calls": run.external_api_call_count,
            "orders_created": run.order_creation_count,
            "version": run.version,
        }

    def results(
        self,
        run_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
        admission_state: str | None = None,
        strategy_status: str | None = None,
    ) -> dict[str, Any]:
        filters = [AdmissionV3Result.run_id == run_id]
        if admission_state:
            filters.append(AdmissionV3Result.admission_state == admission_state)
        if strategy_status:
            filters.append(AdmissionV3Result.strategy_status == strategy_status)
        total = self.session.scalar(select(func.count()).select_from(AdmissionV3Result).where(*filters)) or 0
        rows = list(
            self.session.scalars(
                select(AdmissionV3Result)
                .where(*filters)
                .order_by(AdmissionV3Result.final_score.desc(), AdmissionV3Result.quant_rank)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return {
            "items": [self._result_payload(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def detail(self, run_id: str, stock_code: str) -> dict[str, Any]:
        code = normalize_ts_code(stock_code)
        row = self.session.scalar(
            select(AdmissionV3Result).where(
                AdmissionV3Result.run_id == run_id,
                AdmissionV3Result.stock_code == code,
            )
        )
        if row is None:
            raise ValueError("ADMISSION_V3_RESULT_NOT_FOUND")
        factors = list(
            self.session.scalars(
                select(FactorAttribution)
                .where(FactorAttribution.run_id == run_id, FactorAttribution.stock_code == code)
                .order_by(FactorAttribution.rank_contribution.desc())
            )
        )
        contract = self.session.get(StrategyTimingContract, row.timing_contract_id)
        return {
            **self._result_payload(row),
            "timing_contract": _contract_payload(contract),
            "factor_attribution": [_factor_payload(item) for item in factors],
        }

    def gates(self, run_id: str) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(GateEvaluation)
                .where(GateEvaluation.run_id == run_id)
                .order_by(GateEvaluation.blocked_count.desc(), GateEvaluation.gate_name)
            )
        )
        return [
            {
                "gate_name": row.gate_name,
                "blocked_count": row.blocked_count,
                "affected_count": int((row.counterfactual_json or {}).get("affected_count", row.blocked_count)),
                "evaluated_count": row.evaluated_count,
                "future_return": _optional_number(row.future_return),
                "avoided_loss": _number(row.avoided_loss),
                "missed_gain": _number(row.missed_gain),
                "net_gate_value": _number(row.net_gate_value),
                "counterfactual": row.counterfactual_json,
                "version": row.version,
            }
            for row in rows
        ]

    def _contract(
        self,
        source: EntryTimingV2Result,
        quant: QuantRun,
        eligible_date: date,
        *,
        available_at_ts: datetime,
        signal_generated_at: datetime,
    ) -> StrategyTimingContract:
        spec = StrategyTimingContractSpec(
            stock_code=normalize_ts_code(source.stock_code),
            trade_date=source.trade_date,
            observation_end_ts=datetime.combine(source.trade_date, time(15, 0), SHANGHAI),
            available_at_ts=available_at_ts,
            signal_generated_at=signal_generated_at,
            order_eligible_at=datetime.combine(eligible_date, time(9, 30), SHANGHAI),
            execution_policy="NEXT_TRADING_DAY_OPEN_SHADOW",
            feature_version="decision_explainability_v3_shadow_2_actual_timing",
            data_snapshot_id=quant.data_manifest_id,
            universe_snapshot_id=f"{quant.run_id}:{quant.universe_count}",
        )
        validate_timing_contract(spec)
        existing = self.session.scalar(
            select(StrategyTimingContract).where(
                StrategyTimingContract.stock_code == spec.stock_code,
                StrategyTimingContract.trade_date == spec.trade_date,
                StrategyTimingContract.feature_version == spec.feature_version,
                StrategyTimingContract.data_snapshot_id == spec.data_snapshot_id,
                StrategyTimingContract.universe_snapshot_id == spec.universe_snapshot_id,
            )
        )
        if existing:
            validate_timing_contract(existing)
            return existing
        row = StrategyTimingContract(**spec.__dict__)
        self.session.add(row)
        self.session.flush()
        return row

    def _source_run(self, trade_date: date, run_id: str | None) -> AdmissionV2Run:
        query = select(AdmissionV2Run).where(AdmissionV2Run.trade_date == trade_date)
        if run_id:
            query = query.where(AdmissionV2Run.run_id == run_id)
        run = self.session.scalar(query.order_by(AdmissionV2Run.created_at.desc()))
        if run is None:
            raise ValueError("ADMISSION_V3_SOURCE_V2_RUN_NOT_FOUND")
        if not run.shadow_only or run.enabled_in_production:
            raise ValueError("ADMISSION_V3_SOURCE_NOT_SHADOW")
        return run

    def _source_hashes(self, source_run: AdmissionV2Run, quant: QuantRun, quant_rows: list[QuantRankResult]) -> dict[str, str]:
        cohort = self.session.scalar(
            select(SelectionCohort)
            .where(
                SelectionCohort.selection_trade_date == source_run.trade_date,
                SelectionCohort.quant_run_id == quant.run_id,
            )
            .order_by(SelectionCohort.created_at.desc())
        )
        return EntryTimingV2ShadowService(self.session)._source_hashes(quant, quant_rows, cohort)

    @staticmethod
    def _assert_frozen_quant_baseline() -> None:
        actual = {key: float(value) for key, value in load_quant_config().weights.items()}
        if actual != SHADOW_CONFIG["quant_weights"]:
            raise RuntimeError("QUANT_BASELINE_CHANGED")

    @staticmethod
    def _strategy_features(source: EntryTimingV2Result) -> StrategyFeatures:
        raw = (source.diagnostics_json or {}).get("features") or {}
        allowed = StrategyFeatures.__dataclass_fields__
        values = {key: value for key, value in raw.items() if key in allowed}
        values.setdefault("data_quality_score", _number((source.data_coverage_json or {}).get("strategy_data_quality")))
        values["risk_flags"] = list(values.get("risk_flags") or source.risk_flags_json or [])
        return StrategyFeatures(**values)

    @staticmethod
    def _source_row_hash(row: EntryTimingV2Result) -> list[Any]:
        return [
            row.stock_code,
            row.quant_rank,
            str(row.quant_score),
            str(row.entry_timing_v2_score),
            row.admission_status_v2,
            row.risk_flags_json,
            row.component_scores_json,
            row.diagnostics_json,
        ]

    @staticmethod
    def _gate_rows(run_id: str, trade_date: date, decisions: dict[str, Any]) -> list[GateEvaluation]:
        effects: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for code, decision in decisions.items():
            for gate_name, counterfactual in decision.counterfactuals.items():
                effects[gate_name].append((code, counterfactual))
            for gate_name, passed in decision.hard_gate_results.items():
                if not passed:
                    effects[gate_name].append((code, {"admission_state": "NON_REMOVABLE_HARD_GATE", "score_delta": 0.0}))
        output: list[GateEvaluation] = []
        for gate_name, items in sorted(effects.items()):
            state_changes = sum(
                value.get("admission_state") not in {decisions[code].admission_state, "NON_REMOVABLE_HARD_GATE"}
                for code, value in items
            )
            hard_blocks = sum(value.get("admission_state") == "NON_REMOVABLE_HARD_GATE" for _, value in items)
            output.append(GateEvaluation(
                run_id=run_id,
                trade_date=trade_date,
                gate_name=gate_name,
                blocked_count=state_changes + hard_blocks,
                evaluated_count=len(decisions),
                future_return=None,
                avoided_loss=0,
                missed_gain=0,
                net_gate_value=0,
                counterfactual_json={
                    "matured_return_status": "PENDING_FORWARD_RETURN",
                    "affected_count": len(items),
                    "affected_stocks": [code for code, _ in items],
                    "state_changes": state_changes,
                    "aggregate_score_delta": round(sum(float(value.get("score_delta", 0.0)) for _, value in items), 6),
                },
                version=GATE_EVALUATION_VERSION,
            ))
        return output

    @staticmethod
    def _result_payload(row: AdmissionV3Result) -> dict[str, Any]:
        return {
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "trade_date": row.trade_date.isoformat(),
            "quant_rank": row.quant_rank,
            "industry": row.industry,
            "admission_state": row.admission_state,
            "strategy_status": row.strategy_status,
            "strategy_probability": row.strategy_probability,
            "hard_gate_results": row.hard_gate_results,
            "risk_penalties": row.risk_penalties,
            "opportunity_components": row.opportunity_components,
            "portfolio_adjustments": row.portfolio_adjustments,
            "counterfactuals": row.counterfactuals,
            "base_score": _number(row.base_score),
            "opportunity_score": _number(row.opportunity_score),
            "final_score": _number(row.final_score),
            "expected_value_score": _optional_number(row.expected_value_score),
            "risk_adjusted_opportunity_score": _optional_number(row.risk_adjusted_opportunity_score),
            "position_multiplier": _number(row.position_multiplier),
            "why_selected": row.selected_reason,
            "why_rejected": row.rejected_reasons,
            "largest_factor": row.largest_factor,
            "largest_gate": row.largest_gate,
            "llm_structured_output": row.llm_structured_output,
            "final_llm_score": _optional_number(row.final_llm_score),
            "shadow_only": row.shadow_only,
            "version": row.version,
        }


def _factor_gate_contributions(decision) -> dict[str, float]:
    sentiment = 0.0
    risk = 0.0
    for name, value in decision.risk_penalties.items():
        if name == "MARKET_RED":
            sentiment -= float(value["score_penalty"])
        else:
            risk -= float(value["score_penalty"])
    for name, value in decision.portfolio_adjustments.items():
        if name == "INDUSTRY_CONCENTRATION":
            sentiment -= float(value["score_penalty"])
        else:
            risk -= float(value["score_penalty"])
    return {SENTIMENT_REGIME: sentiment, RISK_LIQUIDITY: risk}


def _raw_metrics(source: EntryTimingV2Result) -> dict[str, float | None]:
    features = (source.diagnostics_json or {}).get("features") or {}
    components = source.component_scores_json or {}
    return {
        "return_1d": _optional_number(features.get("return_1d")),
        "return_5d": _optional_number(features.get("return_5d")),
        "volume_ratio": _optional_number(features.get("volume_ratio")),
        "position_score": _optional_number(components.get("price_position")),
        "pullback_score": _optional_number(components.get("pullback_quality")),
        "market_emotion_score": _optional_number(source.market_emotion_score),
        "sector_strength": _optional_number(components.get("sector_resonance")),
        "liquidity_score": _optional_number(components.get("liquidity")),
    }


def _contract_payload(row: StrategyTimingContract | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "stock_code": row.stock_code,
        "trade_date": row.trade_date.isoformat(),
        "observation_end_ts": row.observation_end_ts.isoformat(),
        "available_at_ts": row.available_at_ts.isoformat(),
        "signal_generated_at": row.signal_generated_at.isoformat(),
        "order_eligible_at": row.order_eligible_at.isoformat(),
        "execution_policy": row.execution_policy,
        "feature_version": row.feature_version,
        "data_snapshot_id": row.data_snapshot_id,
        "universe_snapshot_id": row.universe_snapshot_id,
        "created_at": row.created_at.isoformat(),
    }


def _factor_payload(row: FactorAttribution) -> dict[str, Any]:
    return {
        "stock_code": row.stock_code,
        "trade_date": row.trade_date.isoformat(),
        "factor_family": row.factor_family,
        "raw_signal": row.raw_signal,
        "normalized_score": _number(row.normalized_score),
        "score_contribution": _number(row.score_contribution),
        "gate_contribution": _number(row.gate_contribution),
        "rank_contribution": _number(row.rank_contribution),
        "interaction_note": row.interaction_note,
        "lineage": row.lineage_json,
        "version": row.version,
    }


def _is_st_name(value: str | None) -> bool:
    normalized = (value or "").upper().replace("*", "")
    return normalized.startswith("ST")


def _unique_source_rows(rows: list[EntryTimingV2Result]) -> list[EntryTimingV2Result]:
    """Keep one stock-level explanation while preserving source snapshots unchanged."""

    selected: dict[str, EntryTimingV2Result] = {}
    for row in rows:
        code = normalize_ts_code(row.stock_code)
        current = selected.get(code)
        candidate_key = (0 if row.pool_type == "AI_POOL" else 1, row.quant_rank or 999999)
        current_key = (
            (0 if current.pool_type == "AI_POOL" else 1, current.quant_rank or 999999)
            if current is not None else (999999, 999999)
        )
        if current is None or candidate_key < current_key:
            selected[code] = row
    return sorted(selected.values(), key=lambda row: (row.quant_rank or 999999, normalize_ts_code(row.stock_code)))


def _next_weekday(value: date) -> date:
    current = value + timedelta(days=1)
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _number(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _optional_number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
