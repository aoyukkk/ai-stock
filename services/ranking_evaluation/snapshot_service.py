from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from database.models.quant_run import QuantRankResult, QuantRun
from database.models.ranking_evaluation import (
    RankingEvaluationSnapshot,
    RankingEvaluationSnapshotItem,
    RankingEvaluationState,
)
from services.ranking_evaluation.constants import (
    EVALUATION_SCOPE,
    EVALUATION_VERSION,
    IMMUTABLE_CONFLICT,
    RETURN_BASIS,
    ROOT_DIR,
    load_config,
    model_name_for,
    original_group,
)
from services.ranking_evaluation.data_quality_service import RankingDataQualityService
from services.ranking_evaluation.market_data_service import RankingMarketDataService
from services.ranking_evaluation.schemas import RankingSourceRow, SnapshotCaptureRequest
from services.ranking_evaluation.trading_calendar_service import RankingTradingCalendarService
from services.ranking_evaluation.utils import stable_hash, write_immutable_text
from stock_codes import display_stock_code, normalize_ts_code


class RankingSnapshotService:
    def __init__(
        self,
        session,
        *,
        calendar: RankingTradingCalendarService | None = None,
        market: RankingMarketDataService | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.config = config or load_config()
        self.calendar = calendar or RankingTradingCalendarService()
        self.market = market or RankingMarketDataService(session)
        self.quality = RankingDataQualityService(session)

    def capture(
        self,
        *,
        trade_date: date,
        source_quant_run_id: str,
        factor_version: str,
        allow_historical_import: bool = False,
    ) -> dict[str, Any]:
        quant = self.session.scalar(
            select(QuantRun).where(QuantRun.run_id == source_quant_run_id)
        )
        if quant is None:
            raise ValueError("SOURCE_QUANT_RUN_NOT_FOUND")
        if quant.status != "COMPLETED" or not quant.no_llm_call_verified:
            raise ValueError("SOURCE_QUANT_RUN_NOT_COMPLETED")
        if quant.base_market_trade_date != trade_date:
            raise ValueError("SOURCE_QUANT_TRADE_DATE_MISMATCH")
        self._validate_version(quant, factor_version)
        rows = list(
            self.session.scalars(
                select(QuantRankResult)
                .where(
                    QuantRankResult.quant_run_id == quant.run_id,
                    QuantRankResult.rank <= int(self.config["top_n"]),
                )
                .order_by(QuantRankResult.rank, QuantRankResult.id)
            )
        )
        source_rows = [
            RankingSourceRow(
                stock_code=row.stock_code,
                original_rank=int(row.rank),
                quant_score=float(row.total_score) if row.total_score is not None else None,
                stock_name=str((row.factor_detail_reference or {}).get("stock_name") or row.stock_code),
                ts_code=normalize_ts_code(row.stock_code),
                source_row_number=index,
                raw_payload={
                    "factor_detail_reference": row.factor_detail_reference or {},
                    "source_rank_result_id": row.id,
                },
            )
            for index, row in enumerate(rows, start=1)
        ]
        request = SnapshotCaptureRequest(
            ranking_trade_date=trade_date,
            source_quant_run_id=quant.run_id,
            factor_version=factor_version,
            evaluation_scope=EVALUATION_SCOPE,
            allow_historical_import=allow_historical_import,
            snapshot_origin=(
                "HISTORICAL_IMPORT" if allow_historical_import else "FORWARD_CAPTURE"
            ),
            generated_at=datetime.now(timezone.utc),
        )
        return self.capture_rows(
            request,
            source_rows,
            source_input_hash=quant.request_hash,
            model_name=model_name_for(factor_version, bool(quant.actionable)),
            score_version=str(quant.factor_version or "UNKNOWN"),
            production_or_shadow="PRODUCTION" if quant.actionable else "SHADOW",
        )

    def capture_rows(
        self,
        request: SnapshotCaptureRequest,
        rows: list[RankingSourceRow],
        *,
        source_input_hash: str,
        model_name: str,
        score_version: str,
        production_or_shadow: str,
    ) -> dict[str, Any]:
        if not bool(self.config.get("enabled", True)):
            return {"status": "DISABLED"}
        if request.evaluation_scope != EVALUATION_SCOPE:
            raise ValueError("INVALID_RANKING_EVALUATION_SCOPE")
        if not request.factor_version:
            raise ValueError("FACTOR_VERSION_REQUIRED")
        if not self.calendar.is_open(request.ranking_trade_date):
            raise ValueError("RANKING_TRADE_DATE_NOT_OPEN")
        generated_at = request.generated_at or datetime.now(timezone.utc)
        top_n = int(self.config["top_n"])
        batch = self.market.load_day(request.ranking_trade_date)
        prepared, issue_specs = self._prepare_rows(rows, batch, top_n)
        snapshot_material = {
            "evaluation_version": EVALUATION_VERSION,
            "ranking_trade_date": request.ranking_trade_date,
            "source_quant_run_id": request.source_quant_run_id,
            "model_name": model_name,
            "score_version": score_version,
            "factor_version": request.factor_version,
            "production_or_shadow": production_or_shadow,
            "price_basis": "OFFICIAL_CLOSE",
            "return_basis": RETURN_BASIS,
            "top_n": top_n,
            "source_input_hash": source_input_hash,
            "evaluation_scope": request.evaluation_scope,
            "rows": prepared,
        }
        snapshot_hash = stable_hash(snapshot_material)
        existing = self.session.scalar(
            select(RankingEvaluationSnapshot).where(
                RankingEvaluationSnapshot.ranking_trade_date
                == request.ranking_trade_date,
                RankingEvaluationSnapshot.factor_version == request.factor_version,
                RankingEvaluationSnapshot.evaluation_scope == request.evaluation_scope,
            )
        )
        if existing:
            if existing.snapshot_hash != snapshot_hash:
                raise ValueError(IMMUTABLE_CONFLICT)
            return self._result(existing, "EXISTING_IMMUTABLE_SNAPSHOT")

        snapshot_id = f"rank-snapshot-{snapshot_hash[:24]}"
        activation_date = self._ensure_activation(request, snapshot_id)
        overall = _overall_status(issue_specs)
        artifact_paths = self._write_snapshot_artifacts(
            request.ranking_trade_date,
            request.factor_version,
            snapshot_id,
            snapshot_material,
            prepared,
        )
        snapshot = RankingEvaluationSnapshot(
            evaluation_version=EVALUATION_VERSION,
            snapshot_id=snapshot_id,
            snapshot_run_id=f"rank-capture-{uuid4().hex}",
            ranking_trade_date=request.ranking_trade_date,
            generated_at=generated_at,
            source_quant_run_id=request.source_quant_run_id,
            model_name=model_name,
            score_version=score_version,
            factor_version=request.factor_version,
            production_or_shadow=production_or_shadow,
            price_basis="OFFICIAL_CLOSE",
            return_basis=RETURN_BASIS,
            top_n=top_n,
            source_input_hash=source_input_hash,
            snapshot_hash=snapshot_hash,
            evaluation_scope=request.evaluation_scope,
            snapshot_origin=request.snapshot_origin,
            overall_data_status=overall,
            raw_artifacts_json=artifact_paths,
        )
        self.session.add(snapshot)
        self.session.flush()
        self.session.add_all(
            RankingEvaluationSnapshotItem(snapshot_id=snapshot.id, **row)
            for row in prepared
        )
        for issue in issue_specs:
            self.quality.record(
                snapshot_id=snapshot.id,
                affected_date=request.ranking_trade_date,
                affected_version=request.factor_version,
                **issue,
            )
        self.session.commit()
        self.session.refresh(snapshot)
        result = self._result(snapshot, "CAPTURED")
        result["activation_date"] = activation_date
        return result

    def _ensure_activation(
        self, request: SnapshotCaptureRequest, snapshot_id: str
    ) -> date:
        state = self.session.scalar(
            select(RankingEvaluationState).where(
                RankingEvaluationState.evaluation_version == EVALUATION_VERSION
            )
        )
        if state:
            if (
                request.ranking_trade_date < state.activation_date
                and not request.allow_historical_import
            ):
                raise ValueError("HISTORICAL_IMPORT_REQUIRES_EXPLICIT_FLAG")
            return state.activation_date
        configured = self.config.get("activation_date")
        activation = (
            date.fromisoformat(str(configured))
            if configured
            else request.ranking_trade_date
        )
        if request.ranking_trade_date < activation and not request.allow_historical_import:
            raise ValueError("HISTORICAL_IMPORT_REQUIRES_EXPLICIT_FLAG")
        self.session.add(
            RankingEvaluationState(
                evaluation_version=EVALUATION_VERSION,
                activation_date=activation,
                activated_by_snapshot_id=snapshot_id,
                configuration_hash=stable_hash(self.config),
            )
        )
        self.session.flush()
        return activation

    def _prepare_rows(self, rows, batch, top_n):
        stock_counts = Counter()
        rank_counts = Counter()
        normalized: list[dict[str, Any]] = []
        global_issues: list[dict[str, Any]] = []
        for index, source in enumerate(rows, start=1):
            try:
                ts_code = normalize_ts_code(source.ts_code or source.stock_code)
                stock_code = display_stock_code(ts_code)
            except ValueError:
                ts_code = str(source.ts_code or source.stock_code)
                stock_code = str(source.stock_code)
            stock_counts[stock_code] += 1
            rank_counts[int(source.original_rank)] += 1
            normalized.append(
                {
                    "stock_code": stock_code,
                    "ts_code": ts_code,
                    "stock_name": source.stock_name or stock_code,
                    "original_rank": int(source.original_rank),
                    "quant_score": source.quant_score,
                    "source_row_number": int(source.source_row_number or index),
                }
            )
        if len(normalized) != top_n:
            global_issues.append(
                _issue(
                    "TOP100_COUNT_MISMATCH",
                    "ABNORMAL",
                    f"Expected {top_n} source rows, preserved {len(normalized)}.",
                )
            )
        expected = set(range(1, top_n + 1))
        actual = {row["original_rank"] for row in normalized}
        if actual != expected:
            global_issues.append(
                _issue(
                    "RANK_SEQUENCE_BROKEN",
                    "ABNORMAL",
                    f"Missing ranks={sorted(expected - actual)}; extra ranks={sorted(actual - expected)}.",
                )
            )
        if batch.invalid_date_rows:
            global_issues.append(
                _issue(
                    "WRONG_TRADE_DATE",
                    "ABNORMAL",
                    f"Daily cache contains {len(batch.invalid_date_rows)} rows with a wrong trade date.",
                )
            )
        prepared = []
        for row in normalized:
            codes: list[str] = []
            details: list[str] = []
            if stock_counts[row["stock_code"]] > 1:
                codes.append("DUPLICATE_STOCK")
                details.append("source stock code is duplicated")
            if rank_counts[row["original_rank"]] > 1:
                codes.append("DUPLICATE_RANK")
                details.append("source rank is duplicated")
            if row["quant_score"] is None:
                codes.append("QUANT_SCORE_MISSING")
                details.append("quant score is missing")
            if row["original_rank"] not in expected:
                codes.append("RANK_OUT_OF_SCOPE")
                details.append("rank is outside 1-100")
            price = batch.rows.get(row["ts_code"])
            if row["ts_code"] in batch.duplicate_codes:
                codes.append("DUPLICATE_SOURCE_DATA")
                details.append("baseline daily source has duplicate rows")
            if price is None or price.close is None:
                codes.append("BASELINE_CLOSE_MISSING")
                details.append("official baseline close is missing")
            status = "NORMAL" if not codes else "ABNORMAL"
            prepared.append(
                {
                    **row,
                    "baseline_trade_date": batch.trade_date,
                    "baseline_close": price.close if price else None,
                    "baseline_price_source": price.source if price else "MISSING",
                    "baseline_source_hash": price.source_hash if price else batch.source_hash,
                    "original_group": original_group(row["original_rank"]),
                    "row_data_status": status,
                    "row_issue_code": "|".join(codes) or None,
                    "row_issue_detail": "; ".join(details) or None,
                }
            )
            for code, detail in zip(codes, details):
                global_issues.append(
                    _issue(
                        code,
                        "ABNORMAL" if code.startswith("DUPLICATE") else "WARNING",
                        detail,
                        affected_stock=row["stock_code"],
                    )
                )
        return prepared, _dedupe_issue_specs(global_issues)

    def _write_snapshot_artifacts(
        self,
        trade_date: date,
        factor_version: str,
        snapshot_id: str,
        material: dict[str, Any],
        rows: list[dict[str, Any]],
    ) -> dict[str, str]:
        root = (
            ROOT_DIR
            / str(self.config["snapshot_output_root"])
            / trade_date.isoformat()
            / _safe_token(factor_version)
        )
        json_path = root / f"{snapshot_id}.json"
        csv_path = root / f"{snapshot_id}.csv"
        json_hash = write_immutable_text(
            json_path,
            json.dumps(material, ensure_ascii=False, indent=2, default=str),
        )
        buffer = io.StringIO(newline="")
        headers = list(rows[0]) if rows else [
            "stock_code",
            "ts_code",
            "stock_name",
            "original_rank",
            "quant_score",
        ]
        writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        csv_hash = write_immutable_text(csv_path, buffer.getvalue())
        return {
            "json": str(json_path.resolve()),
            "json_hash": json_hash,
            "csv": str(csv_path.resolve()),
            "csv_hash": csv_hash,
        }

    @staticmethod
    def _validate_version(quant: QuantRun, requested: str) -> None:
        upper = requested.upper()
        source = str(quant.factor_version or "").upper()
        if requested == "TUSHARE_BASELINE_V1" and not quant.actionable:
            raise ValueError("FACTOR_VERSION_SOURCE_MISMATCH")
        if requested == "TUSHARE_QUANT_V2_CORRECTED_SHADOW" and (
            requested not in source and requested != source
        ):
            raise ValueError("FACTOR_VERSION_SOURCE_MISMATCH")
        if requested not in {
            "TUSHARE_BASELINE_V1",
            "TUSHARE_QUANT_V2_CORRECTED_SHADOW",
        } and requested != str(quant.factor_version or ""):
            raise ValueError("FACTOR_VERSION_SOURCE_MISMATCH")

    @staticmethod
    def _result(snapshot: RankingEvaluationSnapshot, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_run_id": snapshot.snapshot_run_id,
            "ranking_trade_date": snapshot.ranking_trade_date,
            "source_quant_run_id": snapshot.source_quant_run_id,
            "factor_version": snapshot.factor_version,
            "snapshot_hash": snapshot.snapshot_hash,
            "data_status": snapshot.overall_data_status,
            "artifacts": snapshot.raw_artifacts_json,
            "shadow_only": True,
            "orders": 0,
            "llm_calls": 0,
            "scheduler": False,
        }


def _issue(code, level, detail, affected_stock=None):
    return {
        "issue_code": code,
        "issue_level": level,
        "detail": detail,
        "affected_stock": affected_stock,
    }


def _dedupe_issue_specs(values):
    result = []
    seen = set()
    for value in values:
        key = stable_hash(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _overall_status(issues):
    if any(row["issue_level"] == "ABNORMAL" for row in issues):
        return "ABNORMAL"
    if issues:
        return "WARNING"
    return "NORMAL"


def _safe_token(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)[:80]
