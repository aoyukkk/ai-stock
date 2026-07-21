from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from backend.core.runtime_paths import tushare_cache_root
from database.models.entry_timing import AdmissionRun, EntryTimingResult
from database.models.market_review import MarketDailySnapshot
from database.models.performance import SelectionCohort, SelectionCohortMember
from database.models.quant_run import QuantRankResult, QuantRun
from database.models.stock import StockMaster
from database.models.validation import ModelValidationSample
from database.models.workbench import ManualSelectionRecord
from entry_timing.admission import BuyAdmissionEngine
from entry_timing.engine import EntryTimingEngine, EntryTimingInput
from entry_timing.flash_v5_shadow import build_flash_v5_shadow_payload
from stock_codes import normalize_ts_code


ROOT = Path(__file__).resolve().parents[1]


def load_entry_timing_config() -> tuple[dict[str, Any], dict[str, Any]]:
    entry = yaml.safe_load((ROOT / "config" / "entry_timing.yaml").read_text(encoding="utf-8"))["entry_timing"]
    threshold = yaml.safe_load((ROOT / "config" / "candidate_threshold.yaml").read_text(encoding="utf-8"))["candidate_threshold"]
    return entry, threshold


class EntryTimingShadowService:
    """Read-only market analysis that persists immutable shadow admission results."""

    def __init__(self, session, *, cache_root: Path | None = None) -> None:
        self.session = session
        self.cache_root = Path(cache_root or tushare_cache_root())
        self.config, self.threshold_config = load_entry_timing_config()
        self.engine = EntryTimingEngine(self.config)
        self.admission = BuyAdmissionEngine(self.config)

    def run(
        self,
        trade_date: date,
        *,
        quant_run_id: str | None = None,
        candidate_mode: str = "QUANT_TOP100",
        force_shadow: bool = False,
    ) -> dict[str, Any]:
        if not force_shadow and not bool(self.config.get("enabled", False)):
            raise ValueError("ENTRY_TIMING_DISABLED_SHADOW_ONLY")
        quant = self._quant_run(trade_date, quant_run_id)
        quant_rows = list(self.session.scalars(
            select(QuantRankResult).where(QuantRankResult.quant_run_id == quant.run_id).order_by(QuantRankResult.rank)
        ))
        quant_hash_before = _quant_hash(quant, quant_rows)
        selected = self._candidate_rows(trade_date, quant, quant_rows, candidate_mode)
        stock_meta = {
            normalize_ts_code(row.code): row
            for row in self.session.scalars(select(StockMaster))
        }
        market_regime, sector_changes = self._market_context(trade_date)
        input_hash = _hash({
            "trade_date": trade_date.isoformat(), "quant_hash": quant_hash_before,
            "candidate_mode": candidate_mode,
            "candidates": [{key: row.get(key) for key in (
                "stock_code", "pool_type", "selection_source", "quant_rank", "quant_score",
                "risk_score", "manual_score", "flash_score",
            )} for row in selected],
            "config": self.config, "threshold": self.threshold_config,
            "cache": self._cache_hashes(trade_date),
            "market": {"regime": market_regime, "sector_changes": sector_changes},
            "stock_metadata": {
                code: {
                    "name": getattr(stock_meta.get(code), "name", None),
                    "industry": getattr(stock_meta.get(code), "industry", None),
                }
                for code in sorted({row["stock_code"] for row in selected})
            },
        })
        existing = self.session.scalar(select(AdmissionRun).where(AdmissionRun.input_hash == input_hash))
        if existing is not None:
            return self.summary(existing.run_id)

        run_id = f"admission-{uuid.uuid4().hex[:24]}"
        loader = TradeDateTimingCache(self.cache_root)
        cache = loader.load(trade_date, {row["stock_code"] for row in selected})
        result_rows: list[EntryTimingResult] = []
        for source in selected:
            code = source["stock_code"]
            meta = stock_meta.get(code)
            industry = str(meta.industry) if meta and meta.industry else None
            item = EntryTimingInput(
                stock_code=code,
                quant_score=float(source["quant_score"]),
                quant_rank=source.get("quant_rank"),
                risk_score=source.get("risk_score"),
                bars=cache["bars"].get(code, []),
                daily_basic=cache["daily_basic"].get(code, {}),
                moneyflow=cache["moneyflow"].get(code, {}),
                limit_data=cache["stk_limit"].get(code, {}),
                sector_change=sector_changes.get(industry) if industry else None,
                concept_change=None,
                market_regime=market_regime,
                realtime={},
            )
            timing = self.engine.evaluate(item)
            decision = self.admission.decide(item, timing)
            manual_score = source.get("manual_score")
            ai_score = timing.entry_timing_score
            diagnostics = {
                **timing.diagnostics,
                "industry": industry,
                "selection_source": source.get("selection_source"),
                "flash_v5_shadow": build_flash_v5_shadow_payload({
                    "entry_timing_score": timing.entry_timing_score,
                    "diagnostics": timing.diagnostics,
                    "risk_flags": timing.risk_flags,
                    "liquidity_score": timing.liquidity_score,
                }),
                "external_data_source": "LOCAL_TUSHARE_TRADE_DATE_CACHE",
            }
            result_rows.append(EntryTimingResult(
                admission_run_id=run_id, trade_date=trade_date, stock_code=code,
                stock_name=meta.name if meta else source.get("stock_name"), quant_run_id=quant.run_id,
                quant_rank=source.get("quant_rank"), quant_score=source["quant_score"],
                flash_score=source.get("flash_score"), position_score=timing.position_score,
                pullback_score=timing.pullback_score, volume_price_score=timing.volume_price_score,
                sector_score=timing.sector_score, market_score=timing.market_score,
                liquidity_score=timing.liquidity_score, entry_timing_score=timing.entry_timing_score,
                data_quality_score=timing.data_quality_score, admission_status=decision.status,
                risk_flags=timing.risk_flags, block_reasons=decision.reasons,
                pool_type=source["pool_type"], manual_score=manual_score, ai_score=ai_score,
                score_difference=(manual_score - ai_score) if manual_score is not None else None,
                diagnostics=diagnostics, config_version=str(self.config.get("config_version", "entry_timing_v1")),
            ))
        ai_results = [row for row in result_rows if row.pool_type == "AI_POOL"]
        distribution = Counter(row.admission_status for row in ai_results)
        max_count = int(self.threshold_config.get("maximum_count", 20))
        admitted_count = min(max_count, distribution.get("PASS", 0))
        quant_hash_after = _quant_hash(quant, list(self.session.scalars(
            select(QuantRankResult).where(QuantRankResult.quant_run_id == quant.run_id).order_by(QuantRankResult.rank)
        )))
        if quant_hash_before != quant_hash_after:
            raise RuntimeError("QUANT_HASH_CHANGED_DURING_SHADOW_RUN")
        run = AdmissionRun(
            run_id=run_id, trade_date=trade_date, quant_run_id=quant.run_id, input_hash=input_hash,
            config_snapshot={"entry_timing": self.config, "candidate_threshold": self.threshold_config, "candidate_mode": candidate_mode},
            candidate_count=len(ai_results), pass_count=distribution.get("PASS", 0),
            review_count=distribution.get("REVIEW", 0), block_count=distribution.get("BLOCK", 0),
            insufficient_count=distribution.get("DATA_INSUFFICIENT", 0), admitted_count=admitted_count,
            manual_challenge_count=sum(row.pool_type != "AI_POOL" for row in result_rows),
            status="SUCCESS", shadow_only=True, enabled_in_production=False,
            quant_hash_before=quant_hash_before, quant_hash_after=quant_hash_after,
            llm_call_count=0, external_api_call_count=0, completed_at=datetime.now(timezone.utc),
        )
        self.session.add(run)
        self.session.add_all(result_rows)
        self.session.commit()
        return self.summary(run_id)

    def summary(self, run_id: str) -> dict[str, Any]:
        run = self.session.scalar(select(AdmissionRun).where(AdmissionRun.run_id == run_id))
        if run is None:
            raise ValueError("ADMISSION_RUN_NOT_FOUND")
        rows = list(self.session.scalars(select(EntryTimingResult).where(EntryTimingResult.admission_run_id == run_id)))
        sources = Counter(row.pool_type for row in rows)
        return {
            "run_id": run.run_id, "trade_date": run.trade_date.isoformat(), "quant_run_id": run.quant_run_id,
            "status": run.status, "candidate_count": run.candidate_count, "pass_count": run.pass_count,
            "review_count": run.review_count, "block_count": run.block_count,
            "insufficient_count": run.insufficient_count, "admitted_count": run.admitted_count,
            "manual_challenge_count": run.manual_challenge_count, "pool_distribution": dict(sources),
            "model_top20_count": min(20, run.candidate_count),
            "entry_filtered_top20_count": run.admitted_count,
            "final_shadow_pool_count": run.admitted_count,
            "shadow_only": run.shadow_only, "enabled_in_production": run.enabled_in_production,
            "quant_hash_unchanged": run.quant_hash_before == run.quant_hash_after,
            "llm_calls": run.llm_call_count, "external_api_calls": run.external_api_call_count,
            "config_version": (run.config_snapshot.get("entry_timing") or {}).get("config_version"),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }

    def results(self, run_id: str, *, page: int = 1, page_size: int = 100, pool_type: str | None = None) -> dict[str, Any]:
        query = select(EntryTimingResult).where(EntryTimingResult.admission_run_id == run_id)
        if pool_type:
            query = query.where(EntryTimingResult.pool_type == pool_type)
        rows = list(self.session.scalars(query.order_by(EntryTimingResult.entry_timing_score.desc(), EntryTimingResult.quant_rank)))
        start = (page - 1) * page_size
        return {"items": [_result_dict(row) for row in rows[start:start + page_size]], "total": len(rows), "page": page, "page_size": page_size}

    def latest(self, trade_date: date) -> dict[str, Any] | None:
        row = self.session.scalar(select(AdmissionRun).where(AdmissionRun.trade_date == trade_date).order_by(AdmissionRun.created_at.desc()))
        return self.summary(row.run_id) if row else None

    def _quant_run(self, trade_date: date, run_id: str | None) -> QuantRun:
        query = select(QuantRun).where(QuantRun.base_market_trade_date == trade_date, QuantRun.status == "COMPLETED")
        if run_id:
            query = query.where(QuantRun.run_id == run_id)
        row = self.session.scalar(query.order_by(QuantRun.created_at.desc()))
        if row is None:
            raise ValueError("QUANT_RUN_NOT_FOUND")
        return row

    def _candidate_rows(self, trade_date, quant, quant_rows, mode) -> list[dict[str, Any]]:
        by_code = {normalize_ts_code(row.stock_code): row for row in quant_rows}
        manuals = {
            normalize_ts_code(row.stock_code): row
            for row in self.session.scalars(select(ManualSelectionRecord).where(ManualSelectionRecord.trade_date == trade_date))
        }
        if mode == "HISTORICAL_CANDIDATES":
            cohort = self.session.scalar(select(SelectionCohort).where(SelectionCohort.selection_trade_date == trade_date).order_by(SelectionCohort.created_at.desc()))
            if cohort is None:
                raise ValueError("HISTORICAL_SELECTION_COHORT_NOT_FOUND")
            members = list(self.session.scalars(select(SelectionCohortMember).where(SelectionCohortMember.cohort_id == cohort.id)))
            flash_scores = self._flash_scores(cohort.flash_run_id)
            result = []
            for member in members:
                code = normalize_ts_code(member.stock_code)
                quant_row = by_code.get(code)
                source = member.selection_source
                manual = manuals.get(code)
                result.append(self._source_row(
                    code, quant_row, member.stock_name_snapshot,
                    "AI_POOL" if source == "LLM" else "MANUAL_CHALLENGE_POOL",
                    source, manual, flash_scores.get(code),
                ))
            return result
        if mode != "QUANT_TOP100":
            raise ValueError("INVALID_ENTRY_TIMING_CANDIDATE_MODE")
        ai = [self._source_row(normalize_ts_code(row.stock_code), row, None, "AI_POOL", "LLM", None, None) for row in quant_rows[:100]]
        challenge = [
            self._source_row(code, by_code.get(code), None, "MANUAL_CHALLENGE_POOL", "MANUAL", manual, None)
            for code, manual in manuals.items() if by_code.get(code) is not None
        ]
        return ai + challenge

    @staticmethod
    def _source_row(code, quant_row, name, pool, source, manual, flash_score) -> dict[str, Any]:
        if quant_row is None:
            raise ValueError(f"QUANT_RESULT_REQUIRED:{code}")
        manual_score = {"HIGH": 90.0, "MEDIUM": 75.0, "LOW": 60.0}.get(str(getattr(manual, "priority", "")).upper()) if manual else None
        return {
            "stock_code": code, "stock_name": name, "pool_type": pool, "selection_source": source,
            "quant_rank": quant_row.rank, "quant_score": float(quant_row.total_score),
            "risk_score": float(quant_row.risk_score) if quant_row.risk_score is not None else None,
            "manual_score": manual_score, "flash_score": flash_score,
        }

    def _flash_scores(self, flash_run_id: str | None) -> dict[str, float | None]:
        if not flash_run_id:
            return {}
        rows = self.session.scalars(select(ModelValidationSample).where(ModelValidationSample.validation_run_id == flash_run_id))
        return {
            normalize_ts_code(row.stock_code): _float((row.screening_result or {}).get("llm_score"))
            for row in rows
        }

    def _market_context(self, trade_date: date) -> tuple[str, dict[str, float]]:
        row = self.session.scalar(select(MarketDailySnapshot).where(MarketDailySnapshot.trade_date == trade_date).order_by(MarketDailySnapshot.created_at.desc()))
        if row is None:
            return "UNKNOWN", {}
        changes = {
            str(item.get("sector_name")): float(item.get("change_percent"))
            for item in (row.industry_summary_json or {}).get("items", [])
            if item.get("sector_name") and item.get("change_percent") is not None
        }
        return row.market_regime, changes

    def _cache_hashes(self, trade_date: date) -> dict[str, Any]:
        result: dict[str, Any] = {}
        date_key = f"{trade_date:%Y%m%d}"
        for name in ("daily", "adj_factor"):
            paths = [
                path for path in sorted((self.cache_root / "trade_date" / name).glob("*.json"))
                if path.stem <= date_key
            ][-60:]
            result[name] = _hash([
                [path.name, hashlib.sha256(path.read_bytes()).hexdigest()] for path in paths
            ])
        for name in ("daily_basic", "moneyflow", "stk_limit"):
            path = self.cache_root / "trade_date" / name / f"{trade_date:%Y%m%d}.json"
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        return result


class TradeDateTimingCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, trade_date: date, codes: set[str]) -> dict[str, Any]:
        date_key = f"{trade_date:%Y%m%d}"
        daily_paths = [path for path in sorted((self.root / "trade_date" / "daily").glob("*.json")) if path.stem <= date_key][-60:]
        adj_by_day = {path.stem: _rows_by_code(path) for path in sorted((self.root / "trade_date" / "adj_factor").glob("*.json")) if path.stem <= date_key}
        bars: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for path in daily_paths:
            adj = adj_by_day.get(path.stem, {})
            for code, row in _rows_by_code(path).items():
                if code not in codes:
                    continue
                factor = _float((adj.get(code) or {}).get("adj_factor")) or 1.0
                item = dict(row)
                item["adj_close"] = (_float(row.get("close")) or 0) * factor
                bars[code].append(item)
        return {
            "bars": dict(bars),
            "daily_basic": _rows_by_code(self.root / "trade_date" / "daily_basic" / f"{date_key}.json"),
            "moneyflow": _rows_by_code(self.root / "trade_date" / "moneyflow" / f"{date_key}.json"),
            "stk_limit": _rows_by_code(self.root / "trade_date" / "stk_limit" / f"{date_key}.json"),
        }


def _rows_by_code(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return {
        normalize_ts_code(str(row.get("ts_code") or row.get("stock_code") or "")): dict(row)
        for row in value if isinstance(row, dict) and (row.get("ts_code") or row.get("stock_code"))
    } if isinstance(value, list) else {}


def _quant_hash(run: QuantRun, rows: list[QuantRankResult]) -> str:
    return _hash({
        "run_id": run.run_id, "request_hash": run.request_hash, "factor_version": run.factor_version,
        "rows": [[row.stock_code, row.rank, str(row.total_score), str(row.technical_score), str(row.capital_score), str(row.emotion_score), str(row.momentum_score), str(row.risk_score)] for row in rows],
    })


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _result_dict(row: EntryTimingResult) -> dict[str, Any]:
    return {
        "stock_code": row.stock_code, "stock_name": row.stock_name, "trade_date": row.trade_date.isoformat(),
        "quant_rank": row.quant_rank, "quant_score": float(row.quant_score),
        "flash_score": float(row.flash_score) if row.flash_score is not None else None,
        "position_score": float(row.position_score), "pullback_score": float(row.pullback_score),
        "volume_price_score": float(row.volume_price_score), "sector_score": float(row.sector_score),
        "market_score": float(row.market_score), "liquidity_score": float(row.liquidity_score),
        "entry_timing_score": float(row.entry_timing_score), "data_quality_score": float(row.data_quality_score),
        "admission_status": row.admission_status, "risk_flags": row.risk_flags,
        "block_reasons": row.block_reasons, "pool_type": row.pool_type,
        "manual_score": float(row.manual_score) if row.manual_score is not None else None,
        "ai_score": float(row.ai_score) if row.ai_score is not None else None,
        "score_difference": float(row.score_difference) if row.score_difference is not None else None,
        "diagnostics": row.diagnostics, "config_version": row.config_version,
    }
