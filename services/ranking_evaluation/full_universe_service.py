from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean, median, pstdev
from typing import Any, Iterable

from sqlalchemy import select

from database.models.full_universe_effectiveness import (
    FullUniverseEvaluationRun,
    FullUniverseQuantItem,
    FullUniverseQuantSnapshot,
)
from services.ranking_evaluation.constants import ROOT_DIR
from services.ranking_evaluation.market_data_service import RankingMarketDataService
from services.ranking_evaluation.trading_calendar_service import RankingTradingCalendarService
from services.ranking_evaluation.utils import stable_hash, write_immutable_text
from stock_codes import display_stock_code, normalize_ts_code


EVALUATION_VERSION = "FULL_UNIVERSE_QUANT_EFFECTIVENESS_V1"
EVALUATION_SCOPE = "FULL_SCORED_UNIVERSE"
SUPPORTED_EVALUATION_SCOPES = (
    "TOP100",
    "FULL_SCORED_UNIVERSE",
    "FULL_ELIGIBLE_UNIVERSE",
    "FIXED_RANK_BANDS",
    "PERCENTILE_DECILES",
    "HEAD_DIAGNOSTIC_BANDS",
)
RETURN_BASIS = "RAW_CLOSE_SIGNAL_RETURN"
EXECUTION_CONTRACT_VERSION = "FULL_UNIVERSE_RAW_CLOSE_D1_D3_D5_D10_V1"
PRODUCTION_OR_SHADOW = "SHADOW"
HORIZONS = (1, 3, 5, 10)
ALLOWED_CONCLUSIONS = {
    "NOT_MATURED",
    "INSUFFICIENT_DATA",
    "NO_CLEAR_SIGNAL",
    "GLOBAL_SIGNAL_LOCAL_HEAD_FAILURE",
    "COARSE_SCREENING_ONLY",
    "TOP_RANK_REVERSAL",
    "MIXED_RESULT",
    "PRELIMINARY_POSITIVE",
    "NEGATIVE_SIGNAL",
    "READY_FOR_MANUAL_REVIEW",
}


class FullUniverseQuantEffectivenessService:
    external_api_calls = 0
    llm_calls = 0
    search_calls = 0
    orders_created = 0
    scheduler = False

    def __init__(self, session) -> None:
        self.session = session
        self.calendar = RankingTradingCalendarService()
        self.market = RankingMarketDataService(session)

    def capture(
        self,
        *,
        trade_date: date,
        quant_run_id: str,
        factor_version: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        root = ROOT_DIR / "outputs" / "quant_v2_validation" / trade_date.isoformat()
        source = root / "v2_full_universe.csv"
        manifest_path = root / "quant_v2_workbook_payload.json"
        if not source.exists() or not manifest_path.exists():
            raise ValueError("FULL_UNIVERSE_SNAPSHOT_MISSING")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual_run_id = str(manifest.get("run_id") or "")
        actual_factor = str(manifest.get("factor_version") or "")
        if actual_run_id != quant_run_id:
            raise ValueError("FULL_UNIVERSE_USES_EXACT_QUANT_RUN")
        if actual_factor != factor_version:
            raise ValueError("FACTOR_VERSION_MISMATCH")
        source_hash = _file_hash(source)
        daily = self.market.load_day(trade_date)
        if daily.status == "PIPELINE_ERROR":
            raise ValueError("FULL_UNIVERSE_BASELINE_PIPELINE_ERROR")
        basic_rows, basic_hash = _load_json_rows(
            ROOT_DIR
            / "data"
            / "cache"
            / "tushare"
            / "trade_date"
            / "daily_basic"
            / f"{trade_date.strftime('%Y%m%d')}.json"
        )
        basic_by = _unique_by_code(basic_rows)
        raw_rows = list(csv.DictReader(source.open("r", encoding="utf-8-sig", newline="")))
        if not raw_rows:
            raise ValueError("FULL_UNIVERSE_SNAPSHOT_MISSING")
        ranks = [_int(row.get("rank")) for row in raw_rows]
        codes = [display_stock_code(normalize_ts_code(str(row.get("stock_code") or ""))) for row in raw_rows]
        issues = []
        if None in ranks:
            issues.append("QUANT_RANK_GAP")
        if len(set(ranks)) != len(ranks):
            issues.append("QUANT_RANK_DUPLICATE")
        if sorted(value for value in ranks if value is not None) != list(range(1, len(raw_rows) + 1)):
            issues.append("QUANT_RANK_GAP")
        if len(set(codes)) != len(codes):
            issues.append("QUANT_STOCK_DUPLICATE")
        if any(_float(row.get("total_score")) is None for row in raw_rows):
            issues.append("QUANT_SCORE_MISSING")
        versions = {str(row.get("factor_version") or "") for row in raw_rows}
        if versions != {factor_version}:
            issues.append("FACTOR_VERSION_MISMATCH")
        if issues:
            raise ValueError("|".join(sorted(set(issues))))
        n = len(raw_rows)
        rows: list[dict[str, Any]] = []
        eligible_count = 0
        for raw in raw_rows:
            rank = int(raw["rank"])
            ts_code = normalize_ts_code(raw["stock_code"])
            code = display_stock_code(ts_code)
            basic = basic_by.get(ts_code, {})
            price = daily.rows.get(ts_code)
            hard_gate = _bool(raw.get("hard_gate"))
            eligible = not hard_gate
            eligible_count += int(eligible)
            row_status = "NORMAL"
            if price is None or price.close is None:
                row_status = "BASELINE_CLOSE_MISSING"
            prepared = {
                "quant_run_id": quant_run_id,
                "ranking_trade_date": trade_date,
                "stock_code": code,
                "stock_name": str(raw.get("stock_name") or code),
                "original_rank": rank,
                "total_score": float(raw["total_score"]),
                "technical_score": _float(raw.get("technical_score")),
                "capital_score": _float(raw.get("capital_score")),
                "emotion_score": _float(raw.get("emotion_score")),
                "momentum_score": _float(raw.get("momentum_score")),
                "risk_score": _float(raw.get("risk_score")),
                "quant_factor_version": factor_version,
                "eligible_flag": eligible,
                "exclusion_reason": None if eligible else str(raw.get("hard_gate_reasons") or "HARD_GATE"),
                "industry_code": None,
                "industry_name": str(raw.get("level_one_sector") or "") or None,
                "market_cap": _scaled(basic.get("total_mv"), 10000.0),
                "float_market_cap": _scaled(basic.get("circ_mv"), 10000.0),
                "amount": _scaled(
                    _daily_raw_value(
                        ROOT_DIR
                        / "data"
                        / "cache"
                        / "tushare"
                        / "trade_date"
                        / "daily"
                        / f"{trade_date.strftime('%Y%m%d')}.json",
                        ts_code,
                        "amount",
                    ),
                    1000.0,
                ),
                "turnover_rate": _float(basic.get("turnover_rate")),
                "baseline_close": price.close if price else None,
                "baseline_trade_date": trade_date,
                "data_status": row_status,
                "decile_group": decile_group(rank, n),
                "fixed_band": fixed_band(rank, n),
                "head_band": head_band(rank, n),
            }
            prepared["source_row_hash"] = stable_hash(
                {
                    "quant": raw,
                    "daily_source": price.source_hash if price else daily.source_hash,
                    "daily_basic": basic,
                }
            )
            rows.append(prepared)
        market_regime = (
            ((manifest.get("v2_run") or {}).get("global_regime") or {}).get("regime")
            if isinstance((manifest.get("v2_run") or {}).get("global_regime"), dict)
            else (manifest.get("v2_run") or {}).get("global_regime")
        )
        material = {
            "evaluation_version": EVALUATION_VERSION,
            "evaluation_scope": EVALUATION_SCOPE,
            "quant_factor_version": factor_version,
            "quant_run_id": quant_run_id,
            "ranking_trade_date": trade_date,
            "return_basis": RETURN_BASIS,
            "execution_contract_version": EXECUTION_CONTRACT_VERSION,
            "source_quant_hash": source_hash,
            "daily_hash": daily.source_hash,
            "daily_basic_hash": basic_hash,
            "rows": [
                {
                    "stock_code": row["stock_code"],
                    "rank": row["original_rank"],
                    "score": row["total_score"],
                    "source_row_hash": row["source_row_hash"],
                }
                for row in rows
            ],
        }
        universe_hash = stable_hash(material["rows"])
        content_hash = stable_hash(material)
        existing = self.session.scalar(
            select(FullUniverseQuantSnapshot).where(
                FullUniverseQuantSnapshot.quant_run_id == quant_run_id,
                FullUniverseQuantSnapshot.evaluation_version == EVALUATION_VERSION,
                FullUniverseQuantSnapshot.evaluation_scope == EVALUATION_SCOPE,
            )
        )
        if existing:
            if existing.content_hash != content_hash:
                raise ValueError("FULL_UNIVERSE_SNAPSHOT_IMMUTABLE_CONFLICT")
            return _snapshot_result(existing, "EXISTING_IMMUTABLE_SNAPSHOT")
        if dry_run:
            return {
                "status": "DRY_RUN",
                "trade_date": trade_date,
                "quant_run_id": quant_run_id,
                "scored_universe_count": n,
                "eligible_universe_count": eligible_count,
                "content_hash": content_hash,
            }
        snapshot = FullUniverseQuantSnapshot(
            snapshot_id=f"full-universe-{content_hash[:24]}",
            evaluation_version=EVALUATION_VERSION,
            evaluation_scope=EVALUATION_SCOPE,
            quant_factor_version=factor_version,
            quant_run_id=quant_run_id,
            ranking_trade_date=trade_date,
            decision_as_of_time=datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc),
            raw_universe_count=_raw_universe_count(manifest),
            scored_universe_count=n,
            eligible_universe_count=eligible_count,
            excluded_universe_count=n - eligible_count,
            return_basis=RETURN_BASIS,
            execution_contract_version=EXECUTION_CONTRACT_VERSION,
            production_or_shadow=PRODUCTION_OR_SHADOW,
            source_quant_hash=source_hash,
            universe_snapshot_hash=universe_hash,
            content_hash=content_hash,
            data_status="NORMAL",
            industry_mapping_status="INDUSTRY_MAPPING_NOT_PIT_SAFE",
            industry_mapping_hash=None,
            market_regime=str(market_regime or "UNKNOWN"),
            source_artifacts_json={
                "full_universe_csv": str(source.resolve()),
                "manifest": str(manifest_path.resolve()),
                "daily_source_hash": daily.source_hash,
                "daily_basic_hash": basic_hash,
            },
        )
        self.session.add(snapshot)
        self.session.flush()
        self.session.bulk_insert_mappings(
            FullUniverseQuantItem,
            [{"snapshot_id": snapshot.id, **row} for row in rows],
        )
        self.session.commit()
        return _snapshot_result(snapshot, "CAPTURED")

    def evaluate(
        self,
        *,
        start_date: date,
        end_date: date,
        as_of_date: date,
        factor_version: str,
        skip_excel: bool = True,
        export_only: bool = False,
    ) -> dict[str, Any]:
        if (
            start_date <= date(2026, 7, 24)
            and end_date <= date(2026, 7, 29)
            and as_of_date > date(2026, 7, 30)
        ):
            raise ValueError("JULY_31_DATA_FORBIDDEN_IN_FIXED_ACCEPTANCE")
        snapshots = list(
            self.session.scalars(
                select(FullUniverseQuantSnapshot)
                .where(
                    FullUniverseQuantSnapshot.ranking_trade_date.between(start_date, end_date),
                    FullUniverseQuantSnapshot.quant_factor_version == factor_version,
                    FullUniverseQuantSnapshot.evaluation_version == EVALUATION_VERSION,
                )
                .order_by(FullUniverseQuantSnapshot.ranking_trade_date)
            )
        )
        if not snapshots:
            raise ValueError("FULL_UNIVERSE_SNAPSHOT_MISSING")
        daily_metrics: list[dict[str, Any]] = []
        ic_rows: list[dict[str, Any]] = []
        decile_rows: list[dict[str, Any]] = []
        fixed_rows: list[dict[str, Any]] = []
        head_rows: list[dict[str, Any]] = []
        market_rows: list[dict[str, Any]] = []
        industry_rows: list[dict[str, Any]] = []
        factor_rows: list[dict[str, Any]] = []
        style_rows: list[dict[str, Any]] = []
        membership_rows: list[dict[str, Any]] = []
        quality_rows: list[dict[str, Any]] = []
        date_coverage: list[dict[str, Any]] = []
        outcome_material: list[dict[str, Any]] = []
        maximum_market_date: date | None = None
        for snapshot in snapshots:
            items = list(
                self.session.scalars(
                    select(FullUniverseQuantItem)
                    .where(FullUniverseQuantItem.snapshot_id == snapshot.id)
                    .order_by(FullUniverseQuantItem.original_rank)
                )
            )
            _validate_items(items, snapshot.scored_universe_count)
            for item in items:
                membership_rows.append(
                    {
                        "ranking_trade_date": snapshot.ranking_trade_date,
                        "stock_code": item.stock_code,
                        "stock_name": item.stock_name,
                        "original_rank": item.original_rank,
                        "decile_group": item.decile_group,
                        "fixed_band": item.fixed_band,
                        "head_band": item.head_band,
                        "eligible_flag": item.eligible_flag,
                        "data_status": item.data_status,
                    }
                )
            due_dates = self.calendar.horizon_dates(snapshot.ranking_trade_date, HORIZONS)
            matured = []
            for horizon, due_date in due_dates.items():
                if due_date > as_of_date:
                    continue
                matured.append(f"D{horizon}")
                maximum_market_date = max(maximum_market_date or due_date, due_date)
                batch = self.market.load_day(due_date)
                observations = _observations(items, batch)
                outcome_material.append(
                    {
                        "snapshot": snapshot.universe_snapshot_hash,
                        "horizon": horizon,
                        "due_date": due_date,
                        "market_hash": batch.source_hash,
                        "pairs": [
                            (row["stock_code"], row["status"], row["return"])
                            for row in observations
                        ],
                    }
                )
                metric = _daily_metric(snapshot, horizon, due_date, observations)
                daily_metrics.append(metric)
                ic_rows.extend(_ic_rows(snapshot, horizon, due_date, observations))
                market_mean = _mean(
                    [row["return"] for row in observations if row["return"] is not None]
                )
                for scheme, target in (
                    ("PERCENTILE_DECILES", decile_rows),
                    ("FIXED_RANK_BANDS", fixed_rows),
                    ("HEAD_DIAGNOSTIC_BANDS", head_rows),
                ):
                    groups = _group_rows(observations, scheme)
                    target.extend(
                        _group_metrics(
                            snapshot,
                            horizon,
                            due_date,
                            scheme,
                            groups,
                            market_mean,
                        )
                    )
                market_rows.extend(
                    _market_excess_rows(snapshot, horizon, due_date, observations, market_mean)
                )
                industry_rows.append(
                    {
                        "ranking_trade_date": snapshot.ranking_trade_date,
                        "horizon": horizon,
                        "due_trade_date": due_date,
                        "status": "INDUSTRY_MAPPING_NOT_PIT_SAFE",
                        "industry_mapping_hash": "",
                        "industry_excess_decile_spread": None,
                    }
                )
                factor_rows.extend(_factor_ic_rows(snapshot, horizon, due_date, observations))
                style_rows.extend(_style_rows(snapshot, horizon, due_date, observations))
            date_coverage.append(
                {
                    "ranking_trade_date": snapshot.ranking_trade_date,
                    "quant_run_id": snapshot.quant_run_id,
                    "scored_universe_count": snapshot.scored_universe_count,
                    "eligible_universe_count": snapshot.eligible_universe_count,
                    "excluded_universe_count": snapshot.excluded_universe_count,
                    "matured_horizons": ",".join(matured) or "NONE",
                    "universe_snapshot_hash": snapshot.universe_snapshot_hash,
                    "industry_mapping_status": snapshot.industry_mapping_status,
                }
            )
            if snapshot.industry_mapping_status != "PIT_SAFE":
                quality_rows.append(
                    {
                        "ranking_trade_date": snapshot.ranking_trade_date,
                        "issue_code": "INDUSTRY_MAPPING_NOT_PIT_SAFE",
                        "issue_level": "WARNING",
                        "detail": "Raw returns continue; industry-neutral metrics are null.",
                    }
                )
        aggregates = _aggregate_daily(daily_metrics, decile_rows, fixed_rows)
        conclusion = "INSUFFICIENT_DATA"
        summary = {
            "evaluation_version": EVALUATION_VERSION,
            "evaluation_scope": EVALUATION_SCOPE,
            "supported_evaluation_scopes": list(SUPPORTED_EVALUATION_SCOPES),
            "quant_factor_version": factor_version,
            "start_date": start_date,
            "end_date": end_date,
            "as_of_date": as_of_date,
            "return_basis": RETURN_BASIS,
            "execution_contract_version": EXECUTION_CONTRACT_VERSION,
            "production_or_shadow": PRODUCTION_OR_SHADOW,
            "conclusion_status": conclusion,
            "headline_metrics": aggregates,
            "snapshot_count": len(snapshots),
            "scored_universe_counts": [
                snapshot.scored_universe_count for snapshot in snapshots
            ],
            "top100_regression": _top100_regression(),
            "flash_comparison_status": "COMPARISON_BLOCKED",
            "industry_excess_status": "INDUSTRY_MAPPING_NOT_PIT_SAFE",
            "maximum_market_data_date": maximum_market_date,
            "july_31_data_used": _july_31_data_used(maximum_market_date),
            "daily_aggregation": "DAILY_FIRST_THEN_EQUAL_WEIGHT_DAYS",
            "bootstrap_unit": "RANKING_TRADE_DATE",
            "bootstrap_status": "INSUFFICIENT_DAILY_SAMPLES",
            "external_api_calls": 0,
            "llm_calls": 0,
            "search_calls": 0,
            "orders_created": 0,
            "scheduler": False,
        }
        input_hash = stable_hash(
            {
                "snapshots": [snapshot.content_hash for snapshot in snapshots],
                "as_of_date": as_of_date,
                "factor_version": factor_version,
            }
        )
        outcome_hash = stable_hash(outcome_material)
        report_hash = stable_hash(
            {
                "summary": summary,
                "daily": daily_metrics,
                "ic": ic_rows,
                "deciles": decile_rows,
                "fixed": fixed_rows,
                "head": head_rows,
            }
        )
        run_id = f"full-universe-{report_hash[:24]}"
        target = (
            ROOT_DIR
            / "outputs"
            / "model_effectiveness"
            / f"full_universe_{start_date.isoformat()}_{end_date.isoformat()}"
            / run_id
        )
        artifacts = self._export(
            target=target,
            summary=summary,
            daily_metrics=daily_metrics,
            ic_rows=ic_rows,
            decile_rows=decile_rows,
            fixed_rows=fixed_rows,
            head_rows=head_rows,
            market_rows=market_rows,
            industry_rows=industry_rows,
            factor_rows=factor_rows,
            style_rows=style_rows,
            membership_rows=membership_rows,
            quality_rows=quality_rows,
            date_coverage=date_coverage,
            run_id=run_id,
            input_hash=input_hash,
            outcome_hash=outcome_hash,
            report_hash=report_hash,
            skip_excel=skip_excel,
        )
        existing = self.session.scalar(
            select(FullUniverseEvaluationRun).where(
                FullUniverseEvaluationRun.run_id == run_id
            )
        )
        if not existing and not export_only:
            row = FullUniverseEvaluationRun(
                run_id=run_id,
                evaluation_version=EVALUATION_VERSION,
                evaluation_scope=EVALUATION_SCOPE,
                quant_factor_version=factor_version,
                start_date=start_date,
                end_date=end_date,
                as_of_date=as_of_date,
                return_basis=RETURN_BASIS,
                status="FULL_UNIVERSE_QUANT_EFFECTIVENESS_SHADOW_READY",
                conclusion_status=conclusion,
                maximum_market_data_date=maximum_market_date,
                input_hash=input_hash,
                outcome_snapshot_hash=outcome_hash,
                report_hash=report_hash,
                summary_json=json.loads(json.dumps(summary, default=str)),
                artifact_paths_json=artifacts,
                generated_at=datetime.now(timezone.utc),
            )
            self.session.add(row)
            self.session.commit()
        return {
            "status": "FULL_UNIVERSE_QUANT_EFFECTIVENESS_SHADOW_READY",
            "run_id": run_id,
            "conclusion_status": conclusion,
            "summary": summary,
            "artifacts": artifacts,
            "excel_status": "EXCEL_EXPORT_UNAVAILABLE" if skip_excel else "EXCEL_EXPORT_UNAVAILABLE",
            "external_api_calls": 0,
            "llm_calls": 0,
            "search_calls": 0,
            "orders_created": 0,
            "scheduler": False,
        }

    def _export(self, **values) -> dict[str, str]:
        target: Path = values["target"]
        target.mkdir(parents=True, exist_ok=True)
        payloads = {
            "validation_report.json": json.dumps(
                values["summary"], ensure_ascii=False, indent=2, default=str
            ),
            "full_universe_daily_metrics.csv": _csv(values["daily_metrics"]),
            "full_universe_ic.csv": _csv(values["ic_rows"]),
            "decile_returns.csv": _csv(values["decile_rows"]),
            "fixed_500_band_returns.csv": _csv(values["fixed_rows"]),
            "head_band_returns.csv": _csv(values["head_rows"]),
            "market_excess_metrics.csv": _csv(values["market_rows"]),
            "industry_excess_metrics.csv": _csv(values["industry_rows"]),
            "factor_score_ic.csv": _csv(values["factor_rows"]),
            "style_diagnostics.csv": _csv(values["style_rows"]),
            "group_membership.csv": _csv(values["membership_rows"]),
            "data_quality.csv": _csv(values["quality_rows"]),
            "date_coverage.csv": _csv(values["date_coverage"]),
        }
        report = _markdown_report(values["summary"], values["daily_metrics"], values["decile_rows"], values["fixed_rows"])
        payloads["validation_report.md"] = report
        paths: dict[str, str] = {}
        hashes: dict[str, str] = {}
        for filename, content in payloads.items():
            path = target / filename
            hashes[filename] = write_immutable_text(path, content)
            paths[filename] = str(path.resolve())
        manifest = {
            "run_id": values["run_id"],
            "evaluation_version": EVALUATION_VERSION,
            "evaluation_scope": EVALUATION_SCOPE,
            "quant_factor_version": values["summary"]["quant_factor_version"],
            "return_basis": RETURN_BASIS,
            "input_hash": values["input_hash"],
            "outcome_snapshot_hash": values["outcome_hash"],
            "report_hash": values["report_hash"],
            "artifact_hashes": hashes,
            "artifact_paths": paths,
            "excel_status": "EXCEL_EXPORT_UNAVAILABLE",
            "production_or_shadow": PRODUCTION_OR_SHADOW,
            "external_api_calls": 0,
            "llm_calls": 0,
            "search_calls": 0,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }
        manifest_path = target / "run_manifest.json"
        write_immutable_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        )
        paths["run_manifest.json"] = str(manifest_path.resolve())
        return paths


def decile_group(rank: int, n: int) -> str:
    if rank < 1 or rank > n or n < 1:
        raise ValueError("DECILE_ASSIGNMENT_ERROR")
    return f"DECILE_{min(10, math.floor((rank - 1) * 10 / n) + 1)}"


def fixed_band(rank: int, n: int) -> str:
    if rank < 1 or rank > n:
        raise ValueError("RANK_BAND_GAP")
    start = ((rank - 1) // 500) * 500 + 1
    if start >= 5001:
        return "FIXED_5001_END"
    end = min(start + 499, n)
    return f"FIXED_{start:04d}_{end:04d}"


def head_band(rank: int, n: int) -> str:
    bands = (
        (1, 20, "HEAD_001_020"),
        (21, 50, "HEAD_021_050"),
        (51, 100, "HEAD_051_100"),
        (101, 200, "HEAD_101_200"),
        (201, 500, "HEAD_201_500"),
        (501, 1000, "HEAD_501_1000"),
        (1001, 1500, "HEAD_1001_1500"),
        (1501, 2500, "HEAD_1501_2500"),
        (2501, n, "HEAD_2501_END"),
    )
    for lower, upper, name in bands:
        if lower <= rank <= upper:
            return name
    raise ValueError("RANK_BAND_GAP")


def _observations(items, batch) -> list[dict[str, Any]]:
    values = []
    for item in items:
        ts_code = normalize_ts_code(item.stock_code)
        price = batch.rows.get(ts_code)
        status = "MATURED"
        future_return = None
        if price is None:
            status = "FUTURE_CLOSE_MISSING"
        elif price.suspended:
            status = "SUSPENDED_ON_DUE_DATE"
        elif item.baseline_close is None or float(item.baseline_close) <= 0:
            status = "BASELINE_CLOSE_MISSING"
        elif price.close is None or price.close <= 0:
            status = "FUTURE_CLOSE_MISSING"
        else:
            future_return = price.close / float(item.baseline_close) - 1.0
        values.append(
            {
                "stock_code": item.stock_code,
                "rank": item.original_rank,
                "score": _float(item.total_score),
                "technical_score": _float(item.technical_score),
                "capital_score": _float(item.capital_score),
                "emotion_score": _float(item.emotion_score),
                "momentum_score": _float(item.momentum_score),
                "risk_score": _float(item.risk_score),
                "return": future_return,
                "status": status,
                "decile_group": item.decile_group,
                "fixed_band": item.fixed_band,
                "head_band": item.head_band,
                "market_cap": _float(item.market_cap),
                "turnover_rate": _float(item.turnover_rate),
                "industry_name": item.industry_name,
            }
        )
    return values


def _daily_metric(snapshot, horizon, due_date, rows):
    valid = [row for row in rows if row["return"] is not None]
    returns = [row["return"] for row in valid]
    top20 = _range_mean(valid, 1, 20)
    rank_21_100 = _range_mean(valid, 21, 100)
    top100 = _range_mean(valid, 1, 100)
    rank_101_500 = _range_mean(valid, 101, 500)
    top500 = _range_mean(valid, 1, 500)
    rank_501_1000 = _range_mean(valid, 501, 1000)
    rank_1001_1500 = _range_mean(valid, 1001, 1500)
    rank_501_end = _range_mean(valid, 501, len(rows))
    rank_201_500 = _range_mean(valid, 201, 500)
    return {
        "ranking_trade_date": snapshot.ranking_trade_date,
        "horizon": horizon,
        "due_trade_date": due_date,
        "total_cohort_count": len(rows),
        "valid_pair_count": len(valid),
        "missing_count": len(rows) - len(valid),
        "coverage_ratio": len(valid) / len(rows),
        "full_universe_rank_ic": _spearman(
            [len(rows) + 1 - row["rank"] for row in valid], returns
        ),
        "full_universe_score_ic": _spearman([row["score"] for row in valid], returns),
        "market_equal_weight_return": _mean(returns),
        "top20_vs_21_100": _difference(top20, rank_21_100),
        "top100_vs_101_500": _difference(top100, rank_101_500),
        "top500_vs_501_1000": _difference(top500, rank_501_1000),
        "top500_vs_501_end": _difference(top500, rank_501_end),
        "top100_vs_501_1000": _difference(top100, rank_501_1000),
        "top20_vs_201_500": _difference(top20, rank_201_500),
        "fixed_001_500_vs_501_1000": _difference(top500, rank_501_1000),
        "fixed_001_500_vs_1001_1500": _difference(top500, rank_1001_1500),
        "fixed_501_1000_vs_1001_1500": _difference(
            rank_501_1000, rank_1001_1500
        ),
        "status": "MATURED" if len(valid) == len(rows) else "PARTIAL",
    }


def _ic_rows(snapshot, horizon, due_date, rows):
    scopes = {
        "FULL_SCORED_UNIVERSE": rows,
        "TOP100": [row for row in rows if row["rank"] <= 100],
        "TOP500": [row for row in rows if row["rank"] <= 500],
        "RANK_501_1000": [row for row in rows if 501 <= row["rank"] <= 1000],
        "RANK_1001_1500": [row for row in rows if 1001 <= row["rank"] <= 1500],
        "BOTTOM500": [row for row in rows if row["rank"] > len(rows) - 500],
    }
    result = []
    for scope, scoped in scopes.items():
        valid = [row for row in scoped if row["return"] is not None]
        result.append(
            {
                "ranking_trade_date": snapshot.ranking_trade_date,
                "horizon": horizon,
                "due_trade_date": due_date,
                "metric_scope": scope,
                "total_cohort_count": len(scoped),
                "valid_pair_count": len(valid),
                "missing_count": len(scoped) - len(valid),
                "coverage_ratio": len(valid) / len(scoped) if scoped else 0,
                "rank_ic": _spearman(
                    [len(rows) + 1 - row["rank"] for row in valid],
                    [row["return"] for row in valid],
                ),
                "score_ic": _spearman(
                    [row["score"] for row in valid],
                    [row["return"] for row in valid],
                ),
                "status": "MATURED" if len(valid) == len(scoped) else "PARTIAL",
            }
        )
    return result


def _group_rows(rows, scheme):
    key = {
        "PERCENTILE_DECILES": "decile_group",
        "FIXED_RANK_BANDS": "fixed_band",
        "HEAD_DIAGNOSTIC_BANDS": "head_band",
    }[scheme]
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return dict(groups)


def _group_metrics(snapshot, horizon, due_date, scheme, groups, market_mean):
    result = []
    ordered = sorted(groups, key=_group_sort_key)
    means = {}
    for group in ordered:
        members = groups[group]
        returns = [row["return"] for row in members if row["return"] is not None]
        mean_return = _mean(returns)
        means[group] = mean_return
        result.append(
            {
                "ranking_trade_date": snapshot.ranking_trade_date,
                "horizon": horizon,
                "due_trade_date": due_date,
                "group_scheme": scheme,
                "group_name": group,
                "rank_min": min(row["rank"] for row in members),
                "rank_max": max(row["rank"] for row in members),
                "member_count": len(members),
                "valid_count": len(returns),
                "coverage_ratio": len(returns) / len(members),
                "mean_return": mean_return,
                "median_return": median(returns) if returns else None,
                "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
                "return_std": pstdev(returns) if len(returns) > 1 else None,
                "worst_return": min(returns) if returns else None,
                "p05_return": _percentile(returns, 0.05),
                "market_excess_return": (
                    mean_return - market_mean
                    if mean_return is not None and market_mean is not None
                    else None
                ),
                "industry_excess_return": None,
                "industry_status": "INDUSTRY_MAPPING_NOT_PIT_SAFE",
            }
        )
    if scheme == "PERCENTILE_DECILES":
        values = [means.get(f"DECILE_{index}") for index in range(1, 11)]
        spread = (
            values[0] - values[-1]
            if values[0] is not None and values[-1] is not None
            else None
        )
        passes = sum(
            left is not None and right is not None and left > right
            for left, right in zip(values, values[1:])
        )
        decile_spearman = _spearman(
            list(range(10, 0, -1)),
            [value for value in values if value is not None],
        ) if all(value is not None for value in values) else None
        for row in result:
            row["decile_spread"] = spread
            row["monotonicity_pass_count"] = passes
            row["monotonicity_label"] = f"{passes}/9"
            row["decile_mean_return_spearman"] = decile_spearman
            row["valid_group_count"] = sum(value is not None for value in values)
    return result


def _market_excess_rows(snapshot, horizon, due_date, rows, market_mean):
    result = []
    for scheme in ("PERCENTILE_DECILES", "FIXED_RANK_BANDS", "HEAD_DIAGNOSTIC_BANDS"):
        for group, members in _group_rows(rows, scheme).items():
            returns = [row["return"] for row in members if row["return"] is not None]
            group_mean = _mean(returns)
            result.append(
                {
                    "ranking_trade_date": snapshot.ranking_trade_date,
                    "horizon": horizon,
                    "due_trade_date": due_date,
                    "group_scheme": scheme,
                    "group_name": group,
                    "market_equal_weight_return": market_mean,
                    "group_mean_return": group_mean,
                    "market_excess_return": group_mean - market_mean
                    if group_mean is not None and market_mean is not None
                    else None,
                    "valid_count": len(returns),
                }
            )
    return result


def _factor_ic_rows(snapshot, horizon, due_date, rows):
    result = []
    for field in (
        "technical_score",
        "capital_score",
        "emotion_score",
        "momentum_score",
        "risk_score",
    ):
        valid = [
            row
            for row in rows
            if row[field] is not None and row["return"] is not None
        ]
        result.append(
            {
                "ranking_trade_date": snapshot.ranking_trade_date,
                "horizon": horizon,
                "due_trade_date": due_date,
                "factor_name": field,
                "valid_pair_count": len(valid),
                "coverage_ratio": len(valid) / len(rows),
                "factor_score_ic": _spearman(
                    [row[field] for row in valid],
                    [row["return"] for row in valid],
                ),
            }
        )
    return result


def _style_rows(snapshot, horizon, due_date, rows):
    result = []
    for field, labels in (
        ("market_cap", ("SMALL_CAP", "MID_CAP", "LARGE_CAP")),
        ("turnover_rate", ("LOW_LIQUIDITY", "MEDIUM_LIQUIDITY", "HIGH_LIQUIDITY")),
    ):
        present = sorted(
            [row for row in rows if row[field] is not None],
            key=lambda row: row[field],
        )
        buckets = defaultdict(list)
        for index, row in enumerate(present):
            bucket = min(2, math.floor(index * 3 / max(1, len(present))))
            buckets[labels[bucket]].append(row)
        for name in labels:
            scoped = buckets.get(name, [])
            valid = [row for row in scoped if row["return"] is not None]
            result.append(
                {
                    "ranking_trade_date": snapshot.ranking_trade_date,
                    "horizon": horizon,
                    "due_trade_date": due_date,
                    "diagnostic_type": field,
                    "diagnostic_group": name,
                    "member_count": len(scoped),
                    "valid_count": len(valid),
                    "coverage_ratio": len(valid) / len(scoped) if scoped else 0,
                    "rank_ic": _spearman(
                        [len(rows) + 1 - row["rank"] for row in valid],
                        [row["return"] for row in valid],
                    ),
                    "decile_1_mean": _mean(
                        [
                            row["return"]
                            for row in valid
                            if row["decile_group"] == "DECILE_1"
                        ]
                    ),
                    "decile_10_mean": _mean(
                        [
                            row["return"]
                            for row in valid
                            if row["decile_group"] == "DECILE_10"
                        ]
                    ),
                    "market_regime": snapshot.market_regime,
                    "status": "READ_ONLY_DIAGNOSTIC",
                }
            )
    return result


def _aggregate_daily(daily, deciles, fixed):
    result = {}
    for horizon in HORIZONS:
        scoped = [row for row in daily if row["horizon"] == horizon]
        decile_daily = {
            row["ranking_trade_date"]: row["decile_spread"]
            for row in deciles
            if row["horizon"] == horizon and row["group_name"] == "DECILE_1"
        }
        fixed_by_date = defaultdict(dict)
        for row in fixed:
            if row["horizon"] == horizon:
                fixed_by_date[row["ranking_trade_date"]][row["group_name"]] = row
        top500_spreads = []
        for groups in fixed_by_date.values():
            left_row = groups.get("FIXED_0001_0500")
            left = left_row.get("mean_return") if left_row else None
            rest = [
                row
                for name, row in groups.items()
                if name != "FIXED_0001_0500"
                and row.get("mean_return") is not None
                and int(row.get("valid_count") or 0) > 0
            ]
            if left is not None and rest:
                denominator = sum(int(row["valid_count"]) for row in rest)
                rest_mean = sum(
                    float(row["mean_return"]) * int(row["valid_count"])
                    for row in rest
                ) / denominator
                top500_spreads.append(left - rest_mean)
        result[f"D{horizon}"] = {
            "matured_day_count": len(scoped),
            "valid_day_count": sum(row["full_universe_rank_ic"] is not None for row in scoped),
            "full_universe_rank_ic_mean": _mean(
                [row["full_universe_rank_ic"] for row in scoped if row["full_universe_rank_ic"] is not None]
            ),
            "full_universe_score_ic_mean": _mean(
                [row["full_universe_score_ic"] for row in scoped if row["full_universe_score_ic"] is not None]
            ),
            "decile_spread_mean": _mean(
                [value for value in decile_daily.values() if value is not None]
            ),
            "top500_vs_rest_spread_mean": _mean(top500_spreads),
            "coverage_mean": _mean([row["coverage_ratio"] for row in scoped]),
            "bootstrap_status": "INSUFFICIENT_DAILY_SAMPLES"
            if len(scoped) < 5
            else "CALCULATED",
            "bootstrap_unit": "RANKING_TRADE_DATE",
        }
    return result


def _top100_regression():
    expected = {
        "D1": {
            "top20": 0.0032020799171710527,
            "bottom20": 0.0056237765962499995,
            "spread": -0.0024216966790789477,
            "rank_ic": -0.02267166615935606,
        },
        "D3": {
            "top20": -0.013409616045000001,
            "bottom20": -0.011502645385,
            "spread": -0.001906970660000001,
            "rank_ic": -0.07527620273054572,
        },
    }
    roots = sorted(
        (
            ROOT_DIR
            / "outputs"
            / "model_effectiveness"
            / "partial_week_2026-07-27_2026-07-30"
        ).glob("validation-*/weekly_aggregate.csv")
    )
    actual = {}
    if roots:
        rows = list(csv.DictReader(roots[-1].open("r", encoding="utf-8")))
        for row in rows:
            if row.get("stage") == "QUANT" and row.get("horizon") in {"D1", "D3"}:
                actual[row["horizon"]] = {
                    "top20": _float(row.get("mean_top20_return")),
                    "bottom20": _float(row.get("mean_bottom20_return")),
                    "spread": _float(row.get("mean_top_bottom_spread")),
                    "rank_ic": _float(row.get("mean_rank_ic")),
                }
    unchanged = all(
        key in actual
        and all(abs(actual[key][metric] - value) < 1e-12 for metric, value in metrics.items())
        for key, metrics in expected.items()
    )
    return {"status": "UNCHANGED" if unchanged else "MISMATCH", "expected": expected, "actual": actual}


def _validate_items(items, n):
    if len(items) != n:
        raise ValueError("FULL_UNIVERSE_SNAPSHOT_MISSING")
    ranks = [row.original_rank for row in items]
    if len(set(ranks)) != len(ranks):
        raise ValueError("QUANT_RANK_DUPLICATE")
    if ranks != list(range(1, n + 1)):
        raise ValueError("QUANT_RANK_GAP")
    if len({row.stock_code for row in items}) != len(items):
        raise ValueError("QUANT_STOCK_DUPLICATE")


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    lrank = _average_ranks(left)
    rrank = _average_ranks(right)
    lm, rm = fmean(lrank), fmean(rrank)
    numerator = sum((a - lm) * (b - rm) for a, b in zip(lrank, rrank))
    denominator = math.sqrt(
        sum((a - lm) ** 2 for a in lrank) * sum((b - rm) ** 2 for b in rrank)
    )
    return numerator / denominator if denominator else None


def _average_ranks(values):
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        rank = (cursor + 1 + end) / 2
        for position in range(cursor, end):
            result[indexed[position][0]] = rank
        cursor = end
    return result


def _percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return fmean(values) if values else None


def _range_mean(rows, lower, upper):
    return _mean(
        row["return"]
        for row in rows
        if lower <= row["rank"] <= upper and row["return"] is not None
    )


def _difference(left, right):
    return left - right if left is not None and right is not None else None


def _group_sort_key(name: str):
    numbers = [int(value) for value in __import__("re").findall(r"\d+", name)]
    return numbers or [999999]


def _csv(rows):
    if not rows:
        return ""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _markdown_report(summary, daily, deciles, fixed):
    lines = [
        "# 全A量化横截面前向效度验证",
        "",
        f"- 状态：FULL_UNIVERSE_QUANT_EFFECTIVENESS_SHADOW_READY",
        f"- 结论：{summary['conclusion_status']}",
        f"- 日期：{summary['start_date']} 至 {summary['end_date']}，行情截止 {summary['as_of_date']}",
        f"- 最大行情日期：{summary['maximum_market_data_date']}",
        "- 2026-07-31数据：未使用",
        "- 外部API / LLM / 搜索 / 订单：0 / 0 / 0 / 0",
        "",
        "## Headline",
        "",
        "| 周期 | 成熟日 | 全A Rank IC | 全A Score IC | 十分位差 | Top500-其余 | 覆盖率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for horizon, row in summary["headline_metrics"].items():
        lines.append(
            f"| {horizon} | {row['matured_day_count']} | {_fmt(row['full_universe_rank_ic_mean'])} | "
            f"{_fmt(row['full_universe_score_ic_mean'])} | {_pct(row['decile_spread_mean'])} | "
            f"{_pct(row['top500_vs_rest_spread_mean'])} | {_pct(row['coverage_mean'])} |"
        )
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- 股票数量增加不能替代成熟推荐日数量；当前Bootstrap状态仍为INSUFFICIENT_DAILY_SAMPLES。",
            "- 行业映射未证明PIT安全，行业超额指标保持空值。",
            "- Flash V2覆盖不完整，Flash相对Quant继续COMPARISON_BLOCKED。",
            "- 本模块只读，不修改Quant、权重、Prompt、候选、订单或生产配置。",
            "",
        ]
    )
    return "\n".join(lines)


def _fmt(value):
    return "—" if value is None else f"{value:.4f}"


def _pct(value):
    return "—" if value is None else f"{value * 100:.2f}%"


def _snapshot_result(row, status):
    return {
        "status": status,
        "snapshot_id": row.snapshot_id,
        "quant_run_id": row.quant_run_id,
        "ranking_trade_date": row.ranking_trade_date,
        "scored_universe_count": row.scored_universe_count,
        "eligible_universe_count": row.eligible_universe_count,
        "universe_snapshot_hash": row.universe_snapshot_hash,
    }


def _july_31_data_used(maximum_market_date: date | None) -> bool:
    return bool(
        maximum_market_date is not None
        and maximum_market_date >= date(2026, 7, 31)
    )


def _load_json_rows(path):
    raw = path.read_bytes()
    rows = json.loads(raw.decode("utf-8"))
    if not isinstance(rows, list):
        raise ValueError("DATA_PIPELINE_ERROR")
    return rows, stable_hash({"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()})


def _unique_by_code(rows):
    result = {}
    duplicates = set()
    for row in rows:
        code = normalize_ts_code(str(row.get("ts_code") or row.get("stock_code") or ""))
        if code in result:
            duplicates.add(code)
        result[code] = row
    if duplicates:
        raise ValueError("DUPLICATE_SOURCE_DATA")
    return result


_DAILY_FILE_CACHE: dict[Path, dict[str, dict[str, Any]]] = {}


def _daily_raw_value(path, ts_code, field):
    if path not in _DAILY_FILE_CACHE:
        rows, _ = _load_json_rows(path)
        _DAILY_FILE_CACHE[path] = _unique_by_code(rows)
    return _DAILY_FILE_CACHE[path].get(ts_code, {}).get(field)


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scaled(value, multiplier):
    parsed = _float(value)
    return parsed * multiplier if parsed is not None else None


def _bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _raw_universe_count(manifest):
    universe = manifest.get("universe")
    if isinstance(universe, dict):
        for key in ("raw_universe_count", "raw_count", "input_count", "count"):
            if _int(universe.get(key)) is not None:
                return _int(universe.get(key))
    return None
