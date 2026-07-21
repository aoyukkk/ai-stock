from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from database.models.market_review import (
    MarketDailySnapshot,
    MarketOutlookScenario,
    MarketReviewDriver,
    MarketReviewEvidence,
    MarketReviewRun,
)
from market_review.schemas import MarketDailyReviewWireV1, MarketDailySnapshotData, MarketEvidence, MarketOutlookResult, MarketRegimeResult


class MarketReviewRepository:
    def __init__(self, session) -> None:
        self.session = session

    def compatible_run(self, trade_date: date, review_input_hash: str) -> MarketReviewRun | None:
        return self.session.scalar(
            select(MarketReviewRun)
            .where(
                MarketReviewRun.trade_date == trade_date,
                MarketReviewRun.review_input_hash == review_input_hash,
                MarketReviewRun.status.in_(["SUCCESS", "PARTIAL_SUCCESS", "DATA_ONLY"]),
            )
            .order_by(MarketReviewRun.created_at.desc())
        )

    def latest_run(self, trade_date: date | None = None, run_id: str | None = None) -> MarketReviewRun | None:
        statement = select(MarketReviewRun)
        if run_id:
            statement = statement.where(MarketReviewRun.run_id == run_id)
        elif trade_date:
            statement = statement.where(MarketReviewRun.trade_date == trade_date)
        return self.session.scalar(statement.order_by(MarketReviewRun.created_at.desc()))

    def save_snapshot(self, snapshot: MarketDailySnapshotData, regime: MarketRegimeResult) -> MarketDailySnapshot:
        existing = self.session.scalar(select(MarketDailySnapshot).where(
            MarketDailySnapshot.trade_date == snapshot.trade_date,
            MarketDailySnapshot.snapshot_hash == snapshot.snapshot_hash,
        ))
        if existing is not None:
            return existing
        row = MarketDailySnapshot(
            trade_date=snapshot.trade_date,
            decision_time=snapshot.decision_time,
            market_direction=snapshot.market_direction,
            market_regime=regime.market_regime,
            regime_score=regime.regime_score,
            regime_confidence=regime.regime_confidence,
            data_quality_score=snapshot.data_quality_score,
            index_summary_json={"items": [item.model_dump(mode="json") for item in snapshot.indices]},
            breadth_summary_json=snapshot.breadth,
            turnover_summary_json=snapshot.turnover,
            limit_summary_json=snapshot.limit_structure,
            industry_summary_json={"items": [item.model_dump(mode="json") for item in snapshot.industries]},
            concept_summary_json={"items": [item.model_dump(mode="json") for item in snapshot.concepts]},
            style_summary_json=snapshot.style,
            capital_summary_json=snapshot.capital,
            technical_summary_json=snapshot.technical,
            metric_ids_json=snapshot.metric_ids,
            source_status_json=snapshot.source_status,
            dataset_watermark_hash=snapshot.dataset_watermark_hash,
            snapshot_hash=snapshot.snapshot_hash,
            schema_version=snapshot.schema_version,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def upsert_evidence(self, evidence: list[MarketEvidence]) -> None:
        for item in evidence:
            row = self.session.scalar(select(MarketReviewEvidence).where(MarketReviewEvidence.evidence_id == item.evidence_id))
            values = {
                "trade_date": item.trade_date,
                "title": item.title,
                "source_name": item.source_name,
                "domain": item.domain,
                "canonical_url": str(item.url),
                "publish_time": item.publish_time,
                "fetched_at": item.fetched_at,
                "short_summary": item.summary,
                "source_tier": item.source_tier,
                "official": item.official,
                "direction": item.direction,
                "affected_scope": item.affected_scope,
                "affected_sectors_json": item.affected_sectors,
                "credibility_score": item.credibility_score,
                "relevance_score": item.relevance_score,
                "timeliness_score": item.timeliness_score,
                "final_evidence_score": item.final_evidence_score,
                "duplicate_group_id": item.duplicate_group_id,
                "status": item.status,
                "content_hash": item.content_hash,
                "provider": item.provider,
            }
            if row is None:
                row = MarketReviewEvidence(evidence_id=item.evidence_id, **values)
                self.session.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)

    def save_run(
        self,
        *,
        run_id: str,
        status: str,
        snapshot_row: MarketDailySnapshot,
        search: dict[str, Any],
        review_input_hash: str,
        evidence_hash: str,
        config: dict[str, Any],
        regime: MarketRegimeResult,
        outlook: MarketOutlookResult,
        wire: MarketDailyReviewWireV1,
        token_usage_id: int | None,
        pipeline_run_id: str | None,
    ) -> MarketReviewRun:
        pro = dict(config.get("pro") or {})
        row = MarketReviewRun(
            run_id=run_id,
            trade_date=wire.trade_date,
            status=status,
            cache_status="SUCCESS",
            snapshot_id=snapshot_row.id,
            search_status=wire.search_status,
            evidence_count=len(search.get("evidence") or []),
            evidence_hash=evidence_hash,
            review_input_hash=review_input_hash,
            model_alias=str(pro.get("model_alias") or "controller-high-capability"),
            actual_model=str(pro.get("actual_model") if token_usage_id else "DATA_ONLY_RULE_RENDERER"),
            prompt_version=str(config.get("prompt_version") or "market_daily_review_prompt_v1"),
            contract_version=str(config.get("contract_version") or "market_daily_review_wire_v1"),
            market_direction=wire.market_direction,
            market_regime=wire.market_regime,
            market_outlook_score=outlook.market_outlook_score,
            base_case_probability=outlook.base_case_probability,
            bull_case_probability=outlook.bull_case_probability,
            bear_case_probability=outlook.bear_case_probability,
            overall_confidence=wire.confidence,
            data_conflict=wire.data_conflict,
            review_summary_json={
                "wire": wire.model_dump(mode="json"),
                "search": {key: value for key, value in search.items() if key != "evidence"},
                "evidence_ids": [item.evidence_id for item in search.get("evidence") or []],
                "regime": regime.model_dump(mode="json"),
                "outlook": outlook.model_dump(mode="json"),
            },
            token_usage_id=token_usage_id,
            pipeline_run_id=pipeline_run_id,
            excel_export_status="NOT_RUN",
            completed_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.flush()
        self._save_drivers(row, wire)
        self._save_scenarios(row, wire)
        self.session.commit()
        return row

    def bundle(self, row: MarketReviewRun) -> dict[str, Any]:
        snapshot = self.session.get(MarketDailySnapshot, row.snapshot_id)
        evidence_ids = list((row.review_summary_json or {}).get("evidence_ids") or [])
        evidence = list(self.session.scalars(
            select(MarketReviewEvidence).where(MarketReviewEvidence.evidence_id.in_(evidence_ids)).order_by(MarketReviewEvidence.final_evidence_score.desc())
        )) if evidence_ids else []
        run_data = _run_dict(row)
        if snapshot is not None:
            run_data["snapshot_hash"] = snapshot.snapshot_hash
        return {
            "run": run_data,
            "snapshot": _snapshot_dict(snapshot) if snapshot else None,
            "review": (row.review_summary_json or {}).get("wire") or {},
            "regime": (row.review_summary_json or {}).get("regime") or {},
            "outlook": (row.review_summary_json or {}).get("outlook") or {},
            "search": (row.review_summary_json or {}).get("search") or {},
            "evidence": [_evidence_dict(item) for item in evidence],
            "drivers": [_driver_dict(item) for item in self.session.scalars(select(MarketReviewDriver).where(MarketReviewDriver.market_review_run_id == row.id).order_by(MarketReviewDriver.rank))],
            "scenarios": [_scenario_dict(item) for item in self.session.scalars(select(MarketOutlookScenario).where(MarketOutlookScenario.market_review_run_id == row.id).order_by(MarketOutlookScenario.id))],
        }

    def history(self, trade_date: date | None = None) -> list[dict[str, Any]]:
        statement = select(MarketReviewRun)
        if trade_date:
            statement = statement.where(MarketReviewRun.trade_date == trade_date)
        rows = list(self.session.scalars(statement.order_by(MarketReviewRun.created_at.desc())))
        return [_run_dict(row) for row in rows]

    def _save_drivers(self, run: MarketReviewRun, wire: MarketDailyReviewWireV1) -> None:
        rank = 1
        for driver_type, items in (("CONFIRMED", wire.confirmed_drivers), ("PROBABLE", wire.probable_explanations), ("STRUCTURAL", wire.structural_observations)):
            for item in items:
                self.session.add(MarketReviewDriver(
                    market_review_run_id=run.id,
                    driver_type=driver_type,
                    title=item.title,
                    direction=item.direction,
                    impact_strength=item.impact_strength,
                    confidence=item.confidence,
                    affected_scope=item.affected_scope,
                    affected_sectors_json=item.affected_sectors,
                    evidence_ids_json=item.evidence_ids,
                    metric_ids_json=item.metric_ids,
                    explanation=item.explanation,
                    rank=rank,
                ))
                rank += 1

    def _save_scenarios(self, run: MarketReviewRun, wire: MarketDailyReviewWireV1) -> None:
        for scenario_type, item in (("BASE", wire.tomorrow_outlook.base_case), ("BULL", wire.tomorrow_outlook.bull_case), ("BEAR", wire.tomorrow_outlook.bear_case)):
            self.session.add(MarketOutlookScenario(
                market_review_run_id=run.id,
                scenario_type=scenario_type,
                probability=item.probability,
                title=item.title,
                description=item.description,
                supporting_reasons_json=item.supporting_reasons,
                triggers_json=item.triggers,
                invalidation_conditions_json=item.invalidation_conditions,
                watch_items_json=item.watch_items,
            ))


def _run_dict(row: MarketReviewRun) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "trade_date": row.trade_date.isoformat(),
        "status": row.status,
        "cache_status": row.cache_status,
        "snapshot_id": row.snapshot_id,
        "snapshot_hash": None,
        "search_status": row.search_status,
        "evidence_count": row.evidence_count,
        "evidence_hash": row.evidence_hash,
        "model_alias": row.model_alias,
        "actual_model": row.actual_model,
        "market_direction": row.market_direction,
        "market_regime": row.market_regime,
        "confidence": row.overall_confidence,
        "base_case_probability": row.base_case_probability,
        "bull_case_probability": row.bull_case_probability,
        "bear_case_probability": row.bear_case_probability,
        "pipeline_run_id": row.pipeline_run_id,
        "excel_export_status": row.excel_export_status,
        "token_usage_id": row.token_usage_id,
        "generated_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _snapshot_dict(row: MarketDailySnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "trade_date": row.trade_date.isoformat(),
        "decision_time": row.decision_time.isoformat(),
        "market_direction": row.market_direction,
        "market_regime": row.market_regime,
        "regime_score": row.regime_score,
        "regime_confidence": row.regime_confidence,
        "data_quality_score": row.data_quality_score,
        "indices": list((row.index_summary_json or {}).get("items") or []),
        "breadth": row.breadth_summary_json,
        "turnover": row.turnover_summary_json,
        "limit_structure": row.limit_summary_json,
        "industries": list((row.industry_summary_json or {}).get("items") or []),
        "concepts": list((row.concept_summary_json or {}).get("items") or []),
        "style": row.style_summary_json,
        "capital": row.capital_summary_json,
        "technical": row.technical_summary_json,
        "source_status": row.source_status_json,
        "metric_ids": row.metric_ids_json,
        "dataset_watermark_hash": row.dataset_watermark_hash,
        "snapshot_hash": row.snapshot_hash,
        "schema_version": row.schema_version,
    }


def _evidence_dict(row: MarketReviewEvidence) -> dict[str, Any]:
    return {
        "evidence_id": row.evidence_id, "trade_date": row.trade_date.isoformat(), "title": row.title, "source_name": row.source_name,
        "domain": row.domain, "url": row.canonical_url,
        "publish_time": row.publish_time.isoformat() if row.publish_time else None,
        "fetched_at": row.fetched_at.isoformat(), "summary": row.short_summary,
        "source_tier": row.source_tier, "official": row.official, "direction": row.direction,
        "affected_scope": row.affected_scope, "affected_sectors": row.affected_sectors_json,
        "credibility_score": row.credibility_score, "relevance_score": row.relevance_score,
        "timeliness_score": row.timeliness_score, "final_evidence_score": row.final_evidence_score,
        "duplicate_group_id": row.duplicate_group_id, "status": row.status,
        "content_hash": row.content_hash, "provider": row.provider,
    }


def _driver_dict(row: MarketReviewDriver) -> dict[str, Any]:
    return {
        "driver_type": row.driver_type, "title": row.title, "direction": row.direction,
        "impact_strength": row.impact_strength, "confidence": row.confidence,
        "affected_scope": row.affected_scope, "affected_sectors": row.affected_sectors_json,
        "evidence_ids": row.evidence_ids_json, "metric_ids": row.metric_ids_json,
        "explanation": row.explanation, "rank": row.rank,
    }


def _scenario_dict(row: MarketOutlookScenario) -> dict[str, Any]:
    return {
        "scenario_type": row.scenario_type, "probability": row.probability, "title": row.title,
        "description": row.description, "supporting_reasons": row.supporting_reasons_json,
        "triggers": row.triggers_json, "invalidation_conditions": row.invalidation_conditions_json,
        "watch_items": row.watch_items_json,
    }
