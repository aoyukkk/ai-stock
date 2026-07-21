from __future__ import annotations

import hashlib
import json
import math
import statistics
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from database.models.entry_timing import AdmissionRun, EntryTimingResult
from database.models.entry_timing_v2 import AdmissionV2Run, EntryTimingV2Result, MarketEmotionSnapshot, StrategyClassificationResult
from database.models.market_review import MarketDailySnapshot
from database.models.performance import SelectionCohort
from database.models.quant_run import QuantRankResult, QuantRun
from database.models.stock import StockMaster
from database.models.validation import ModelValidationSample, ProCandidateReview, ProResumeRun
from entry_timing.admission_v2 import StrategyAwareAdmissionEngine
from entry_timing.config_v2 import EntryTimingV2ConfigService
from entry_timing.engine import EntryTimingAssessment
from entry_timing.market_emotion import MarketEmotionEngine, StrategyMarketEmotionGate
from entry_timing.service import EntryTimingShadowService, TradeDateTimingCache, _quant_hash
from entry_timing.strategy import ShortTermStrategyClassifier, StrategyFeatures
from entry_timing.v2 import EntryTimingV2Engine
from stock_codes import normalize_ts_code


class EntryTimingV2ShadowService:
    def __init__(self, session, *, cache_root=None, app_config=None) -> None:
        self.session = session
        self.config = EntryTimingV2ConfigService(app_config).get()
        v1_service = EntryTimingShadowService(session, cache_root=cache_root)
        self.v1_service = v1_service
        self.cache_root = v1_service.cache_root
        self.cache = TradeDateTimingCache(self.cache_root)
        self.classifier = ShortTermStrategyClassifier(self.config)
        self.emotion_engine = MarketEmotionEngine(self.config)
        self.market_gate = StrategyMarketEmotionGate(self.config)
        self.timing_engine = EntryTimingV2Engine(self.config)
        self.admission = StrategyAwareAdmissionEngine(self.config)

    def run(self, trade_date: date, *, quant_run_id: str | None = None, candidate_mode: str = "HISTORICAL_CANDIDATES", force_shadow: bool = False) -> dict[str, Any]:
        if not force_shadow and not bool(self.config.get("enabled", False)):
            raise ValueError("ENTRY_TIMING_V2_DISABLED_SHADOW_ONLY")
        v1_summary = self.v1_service.run(
            trade_date, quant_run_id=quant_run_id, candidate_mode=candidate_mode, force_shadow=True,
        )
        v1_run = self.session.scalar(select(AdmissionRun).where(AdmissionRun.run_id == v1_summary["run_id"]))
        v1_rows = list(self.session.scalars(
            select(EntryTimingResult).where(EntryTimingResult.admission_run_id == v1_run.run_id)
            .order_by(EntryTimingResult.quant_rank, EntryTimingResult.stock_code)
        ))
        quant = self.session.scalar(select(QuantRun).where(QuantRun.run_id == v1_run.quant_run_id))
        quant_rows = list(self.session.scalars(select(QuantRankResult).where(QuantRankResult.quant_run_id == quant.run_id).order_by(QuantRankResult.rank)))
        quant_by_code = {normalize_ts_code(item.stock_code): item for item in quant_rows}
        stock_master = {
            normalize_ts_code(item.code): item
            for item in self.session.scalars(select(StockMaster).where(StockMaster.code.in_({normalize_ts_code(row.stock_code) for row in v1_rows})))
        }
        cohort = self.session.scalar(select(SelectionCohort).where(
            SelectionCohort.selection_trade_date == trade_date,
            SelectionCohort.quant_run_id == quant.run_id,
        ).order_by(SelectionCohort.created_at.desc()))
        hashes_before = self._source_hashes(quant, quant_rows, cohort)
        market_row = self.session.scalar(select(MarketDailySnapshot).where(MarketDailySnapshot.trade_date == trade_date).order_by(MarketDailySnapshot.created_at.desc()))
        market_payload = self._market_payload(market_row)
        emotion = self.emotion_engine.evaluate(market_payload)
        input_hash = _hash({
            "trade_date": trade_date, "v1_run": v1_run.run_id, "v1_rows": [self._v1_hash_row(row) for row in v1_rows],
            "market_snapshot_hash": market_row.snapshot_hash if market_row else None,
            "config": self.config, "source_hashes": hashes_before,
            "implementation_revision": "entry_timing_v2_1_r2",
        })
        existing = self.session.scalar(select(AdmissionV2Run).where(AdmissionV2Run.input_hash == input_hash))
        if existing is not None:
            return self.summary(existing.run_id)
        run_id = f"admission-v2-{uuid.uuid4().hex[:21]}"
        emotion_row = self._persist_emotion(trade_date, market_row, emotion)
        codes = {row.stock_code for row in v1_rows}
        loaded = self.cache.load(trade_date, codes)
        classifications: dict[str, Any] = {}
        results: list[EntryTimingV2Result] = []
        for row in v1_rows:
            bars = loaded["bars"].get(row.stock_code, [])
            v1 = self._v1_assessment(row)
            features = self._features(bars, v1, emotion.emotion_state, market_row.market_regime if market_row else "UNKNOWN")
            classification = classifications.get(row.stock_code)
            if classification is None:
                classification = self.classifier.classify(features)
                classifications[row.stock_code] = classification
                self.session.add(self._classification_row(run_id, trade_date, row.stock_code, classification))
            v2 = self.timing_engine.evaluate(v1, classification)
            pullback_quality = (v1.pullback_score / 20 * 100) if v1.pullback_score is not None else 0
            sector_score = (v1.sector_score / 15 * 100) if v1.sector_score is not None else 0
            gate_status, gate_reasons, increment = self.market_gate.evaluate(
                classification.strategy_id, emotion.emotion_state,
                market_regime=features.market_regime, strategy_fit=classification.strategy_fit_score,
                pullback_quality=pullback_quality, sector_score=sector_score,
                reversal_confirmation=features.reversal_confirmation is True,
            )
            risk_flags = list(row.risk_flags or [])
            if self._valid_breakout(classification.strategy_id, classification.strategy_fit_score, features, emotion.emotion_state):
                risk_flags = [flag for flag in risk_flags if flag != "HIGH_CHASE_RISK"]
                risk_flags.append("BREAKOUT_VALID")
            risk_score = _number(row.diagnostics.get("risk_score"))
            if risk_score is None:
                quant_result = quant_by_code.get(normalize_ts_code(row.stock_code))
                risk_score = _number(getattr(quant_result, "risk_score", None))
            decision = self.admission.decide(
                quant_score=float(row.quant_score), risk_score=risk_score,
                timing_score=v2.entry_timing_v2_score, strategy_id=classification.strategy_id,
                strategy_fit=classification.strategy_fit_score,
                classification_status=classification.classification_status,
                market_gate_status=gate_status, market_gate_reasons=gate_reasons,
                threshold_increment=increment, risk_flags=risk_flags,
                data_quality=min(float(row.data_quality_score), classification.data_quality_score),
                data_conflicted=False,
            )
            results.append(EntryTimingV2Result(
                run_id=run_id, trade_date=trade_date, stock_code=row.stock_code,
                stock_name=getattr(stock_master.get(normalize_ts_code(row.stock_code)), "name", None) or row.stock_name,
                pool_type=row.pool_type, selection_source=(row.diagnostics or {}).get("selection_source"),
                quant_run_id=row.quant_run_id, quant_rank=row.quant_rank, quant_score=row.quant_score,
                risk_score=risk_score, flash_score=row.flash_score, strategy_id=classification.strategy_id,
                strategy_fit_score=classification.strategy_fit_score, strategy_confidence=classification.strategy_confidence,
                classification_status=classification.classification_status,
                market_emotion_score=emotion.market_emotion_score, market_emotion_state=emotion.emotion_state,
                market_regime=features.market_regime, market_gate_status=decision.market_gate_status,
                entry_timing_v1_score=row.entry_timing_score, entry_timing_v2_score=v2.entry_timing_v2_score,
                admission_ranking_score_v2=decision.admission_ranking_score,
                admission_status_v1=row.admission_status, admission_status_v2=decision.admission_status,
                risk_flags_json=risk_flags, block_reasons_json=decision.block_reasons,
                review_reasons_json=decision.review_reasons, requires_manual_review=decision.requires_manual_review,
                component_scores_json=v2.component_scores,
                data_coverage_json={"v1_data_quality": float(row.data_quality_score), "strategy_data_quality": classification.data_quality_score, "v2_component_coverage": v2.component_coverage},
                diagnostics_json={"features": _feature_dict(features), "classification": classification.details, "effective_pass_threshold": decision.effective_pass_threshold, "effective_review_threshold": decision.effective_review_threshold},
                version=str(self.config["entry_timing_version"]),
            ))
        ai = [row for row in results if row.pool_type == "AI_POOL"]
        counts = Counter(row.admission_status_v2 for row in ai)
        strategy_counts = Counter(row.strategy_id for row in ai)
        maximum = int(self.config["maximum_candidates"])
        admitted = min(maximum, counts.get("PASS", 0))
        hashes_after = self._source_hashes(quant, list(self.session.scalars(select(QuantRankResult).where(QuantRankResult.quant_run_id == quant.run_id).order_by(QuantRankResult.rank))), cohort)
        if hashes_before != hashes_after:
            raise RuntimeError("ENTRY_TIMING_V2_SOURCE_HASH_CHANGED")
        run = AdmissionV2Run(
            run_id=run_id, trade_date=trade_date, quant_run_id=quant.run_id, v1_run_id=v1_run.run_id,
            input_hash=input_hash, config_snapshot=self.config, candidate_count=len(ai),
            pass_count=counts.get("PASS", 0), review_count=counts.get("REVIEW", 0), block_count=counts.get("BLOCK", 0),
            admitted_count=admitted, manual_challenge_count=sum(row.pool_type != "AI_POOL" for row in results),
            market_emotion_score=emotion.market_emotion_score, market_emotion_state=emotion.emotion_state,
            market_regime=market_row.market_regime if market_row else "UNKNOWN", strategy_distribution=dict(strategy_counts),
            status="SUCCESS", shadow_only=True, enabled_in_production=False,
            quant_hash_before=hashes_before["quant"], quant_hash_after=hashes_after["quant"],
            flash_hash_before=hashes_before["flash"], flash_hash_after=hashes_after["flash"],
            pro_hash_before=hashes_before["pro"], pro_hash_after=hashes_after["pro"],
            llm_call_count=0, external_api_call_count=0, order_creation_count=0,
            completed_at=datetime.now(timezone.utc),
        )
        self.session.add(run)
        self.session.add_all(results)
        self.session.commit()
        return self.summary(run_id)

    def summary(self, run_id: str) -> dict[str, Any]:
        row = self.session.scalar(select(AdmissionV2Run).where(AdmissionV2Run.run_id == run_id))
        if row is None: raise ValueError("ADMISSION_V2_RUN_NOT_FOUND")
        return {
            "run_id": row.run_id, "trade_date": row.trade_date.isoformat(), "quant_run_id": row.quant_run_id,
            "v1_run_id": row.v1_run_id, "status": row.status, "candidate_count": row.candidate_count,
            "pass_count": row.pass_count, "review_count": row.review_count, "block_count": row.block_count,
            "admitted_count": row.admitted_count, "manual_challenge_count": row.manual_challenge_count,
            "market_emotion_score": _number(row.market_emotion_score), "market_emotion_state": row.market_emotion_state,
            "market_regime": row.market_regime, "strategy_distribution": row.strategy_distribution,
            "shadow_only": row.shadow_only, "enabled_in_production": row.enabled_in_production,
            "quant_hash_unchanged": row.quant_hash_before == row.quant_hash_after,
            "flash_hash_unchanged": row.flash_hash_before == row.flash_hash_after,
            "pro_hash_unchanged": row.pro_hash_before == row.pro_hash_after,
            "llm_calls": row.llm_call_count, "external_api_calls": row.external_api_call_count,
            "order_creation_count": row.order_creation_count,
            "versions": {key: row.config_snapshot.get(key) for key in ("classifier_version", "market_emotion_version", "entry_timing_version", "admission_version")},
        }

    def latest(self, trade_date: date) -> dict[str, Any] | None:
        row = self.session.scalar(select(AdmissionV2Run).where(AdmissionV2Run.trade_date == trade_date).order_by(AdmissionV2Run.created_at.desc()))
        return self.summary(row.run_id) if row else None

    def results(self, run_id: str, *, page=1, page_size=100, strategy_id=None, emotion_state=None, market_regime=None, admission_status_v1=None, admission_status=None, pool_type=None) -> dict[str, Any]:
        query = select(EntryTimingV2Result).where(EntryTimingV2Result.run_id == run_id)
        for column, value in ((EntryTimingV2Result.strategy_id, strategy_id), (EntryTimingV2Result.market_emotion_state, emotion_state), (EntryTimingV2Result.market_regime, market_regime), (EntryTimingV2Result.admission_status_v1, admission_status_v1), (EntryTimingV2Result.admission_status_v2, admission_status), (EntryTimingV2Result.pool_type, pool_type)):
            if value: query = query.where(column == value)
        rows = list(self.session.scalars(query.order_by(EntryTimingV2Result.admission_ranking_score_v2.desc(), EntryTimingV2Result.quant_rank)))
        start = (page - 1) * page_size
        return {"items": [_result_dict(row) for row in rows[start:start + page_size]], "total": len(rows), "page": page, "page_size": page_size}

    @staticmethod
    def _market_payload(row: MarketDailySnapshot | None) -> dict[str, Any]:
        if row is None: return {}
        return {"breadth": row.breadth_summary_json, "limit_structure": row.limit_summary_json, "turnover": row.turnover_summary_json, "market_regime": row.market_regime, "decision_time": row.decision_time}

    def _persist_emotion(self, trade_date, market_row, emotion):
        existing = self.session.scalar(select(MarketEmotionSnapshot).where(MarketEmotionSnapshot.trade_date == trade_date, MarketEmotionSnapshot.input_hash == emotion.input_hash, MarketEmotionSnapshot.version == emotion.version))
        if existing: return existing
        row = MarketEmotionSnapshot(
            trade_date=trade_date, decision_time=market_row.decision_time if market_row else datetime.combine(trade_date, datetime.min.time(), tzinfo=timezone.utc),
            breadth_health=emotion.breadth_health, limit_structure_health=emotion.limit_structure_health,
            break_board_health=emotion.break_board_health, median_return_health=emotion.median_return_health,
            turnover_health=emotion.turnover_health, tail_risk_health=emotion.tail_risk_health,
            market_emotion_score=emotion.market_emotion_score, emotion_state=emotion.emotion_state,
            market_regime=market_row.market_regime if market_row else "UNKNOWN", component_coverage=emotion.component_coverage,
            missing_components_json=emotion.missing_components, input_hash=emotion.input_hash, version=emotion.version,
        )
        self.session.add(row); self.session.flush(); return row

    @staticmethod
    def _v1_assessment(row: EntryTimingResult) -> EntryTimingAssessment:
        d = row.diagnostics or {}
        return EntryTimingAssessment(
            position_score=float(row.position_score), pullback_score=float(row.pullback_score),
            volume_price_score=float(row.volume_price_score), sector_score=float(row.sector_score),
            market_score=float(row.market_score), liquidity_score=float(row.liquidity_score),
            entry_timing_score=float(row.entry_timing_score), data_quality_score=float(row.data_quality_score),
            timing_status=row.admission_status, risk_flags=list(row.risk_flags or []), diagnostics=d,
        )

    @staticmethod
    def _features(bars, v1, emotion_state, market_regime):
        closes = [_number(row.get("adj_close")) for row in bars]
        closes = [value for value in closes if value is not None]
        close = closes[-1] if closes else None
        ma = lambda n, offset=0: statistics.fmean(closes[-n-offset:-offset or None]) if len(closes) >= n + offset else None
        ma20, old_ma20, ma60, old_ma60 = ma(20), ma(20, 5), ma(60), ma(60, 5)
        rsi = _rsi(closes, 14)
        ret1 = v1.diagnostics.get("return_1d")
        sector_change = v1.diagnostics.get("sector_change")
        sector_score = v1.sector_score / 15 * 100 if sector_change is not None else None
        return StrategyFeatures(
            close=close, ma5=ma(5), ma10=ma(10), ma20=ma20, ma60=ma60,
            ma20_slope=(ma20 / old_ma20 - 1) if ma20 and old_ma20 else None,
            ma60_slope=(ma60 / old_ma60 - 1) if ma60 and old_ma60 else None,
            return_1d=ret1, return_5d=v1.diagnostics.get("return_5d"), return_10d=v1.diagnostics.get("return_10d"),
            distance_20d_high=v1.diagnostics.get("distance_20d_high"), recent_drawdown=v1.diagnostics.get("distance_20d_high"),
            volume_ratio=v1.diagnostics.get("amount_ratio"), pullback_volume_ratio=v1.diagnostics.get("amount_ratio"),
            rsi14=rsi, atr_ratio=None, sector_score=sector_score, sector_breadth=None,
            stock_relative_strength=(ret1 - sector_change) * 100 if ret1 is not None and sector_change is not None else None,
            sector_rank_percentile=None, market_regime=market_regime, market_emotion_state=emotion_state,
            reversal_confirmation=bool(ret1 is not None and ret1 > 0 and close is not None and ma(5) is not None and close >= ma(5)),
            risk_flags=list(v1.risk_flags), data_quality_score=float(v1.data_quality_score),
        )

    @staticmethod
    def _classification_row(run_id, trade_date, code, c):
        return StrategyClassificationResult(
            run_id=run_id, trade_date=trade_date, stock_code=code, strategy_id=c.strategy_id,
            primary_strategy=c.primary_strategy, alternative_strategies_json=c.alternative_strategy_ids,
            strategy_fit_score=c.strategy_fit_score, pattern_fit_score=c.pattern_fit_score,
            regime_compatibility_score=c.regime_compatibility_score, sector_compatibility_score=c.sector_compatibility_score,
            data_quality_score=c.data_quality_score, confidence=c.strategy_confidence,
            matched_conditions_json=c.matched_conditions, failed_conditions_json=c.failed_conditions,
            status=c.classification_status, classifier_version=c.classifier_version, details_json=c.details,
        )

    @staticmethod
    def _valid_breakout(strategy_id, fit, features, emotion_state):
        return strategy_id == "TREND_BREAKOUT" and fit >= 60 and emotion_state == "GREEN" and (features.volume_ratio or 0) >= 1.2 and (features.sector_score or 0) >= 55

    @staticmethod
    def _v1_hash_row(row):
        return [row.stock_code, row.pool_type, str(row.quant_score), str(row.entry_timing_score), row.admission_status, row.risk_flags, row.diagnostics]

    def _source_hashes(self, quant, quant_rows, cohort):
        quant_hash = _quant_hash(quant, quant_rows)
        if cohort is None: return {"quant": quant_hash, "flash": _hash([]), "pro": _hash([])}
        flash = list(self.session.scalars(select(ModelValidationSample).where(ModelValidationSample.validation_run_id == cohort.flash_run_id).order_by(ModelValidationSample.rank)))
        pro_run = self.session.scalar(select(ProResumeRun).where(ProResumeRun.run_id == cohort.pro_run_id))
        pro = list(self.session.scalars(select(ProCandidateReview).where(ProCandidateReview.pro_resume_run_id == cohort.pro_run_id).order_by(ProCandidateReview.pro_rank)))
        return {
            "quant": quant_hash,
            "flash": _hash([[row.stock_code, row.rank, row.screening_result] for row in flash]),
            "pro": _hash([getattr(pro_run, "candidate_set_hash", None), [[row.stock_code, row.pro_rank, str(row.pro_score), row.priority, row.final_summary] for row in pro]]),
        }


def _result_dict(row: EntryTimingV2Result) -> dict[str, Any]:
    return {
        "stock_code": row.stock_code, "stock_name": row.stock_name, "trade_date": row.trade_date.isoformat(),
        "pool_type": row.pool_type, "selection_source": row.selection_source, "quant_rank": row.quant_rank,
        "quant_score": _number(row.quant_score), "risk_score": _number(row.risk_score), "flash_score": _number(row.flash_score),
        "strategy_id": row.strategy_id, "strategy_fit_score": _number(row.strategy_fit_score),
        "strategy_confidence": _number(row.strategy_confidence), "classification_status": row.classification_status,
        "market_emotion_score": _number(row.market_emotion_score), "market_emotion_state": row.market_emotion_state,
        "market_regime": row.market_regime, "market_gate_status": row.market_gate_status,
        "entry_timing_v1_score": _number(row.entry_timing_v1_score), "entry_timing_v2_score": _number(row.entry_timing_v2_score),
        "admission_ranking_score_v2": _number(row.admission_ranking_score_v2),
        "admission_status_v1": row.admission_status_v1, "admission_status_v2": row.admission_status_v2,
        "risk_flags": row.risk_flags_json, "block_reasons": row.block_reasons_json,
        "review_reasons": row.review_reasons_json, "requires_manual_review": row.requires_manual_review,
        "component_scores": row.component_scores_json, "data_coverage": row.data_coverage_json,
        "diagnostics": row.diagnostics_json, "version": row.version,
    }


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _number(value):
    try: return float(value) if value is not None else None
    except (TypeError, ValueError): return None


def _rsi(values, window):
    if len(values) <= window: return None
    changes = [b - a for a, b in zip(values[-window-1:-1], values[-window:])]
    gains = [max(0, value) for value in changes]; losses = [max(0, -value) for value in changes]
    avg_loss = statistics.fmean(losses)
    if avg_loss == 0: return 100.0
    return 100 - 100 / (1 + statistics.fmean(gains) / avg_loss)


def _feature_dict(value: StrategyFeatures):
    return {name: getattr(value, name) for name in value.__dataclass_fields__}
