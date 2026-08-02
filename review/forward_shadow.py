from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    AdmissionV3Result,
    DecisionSnapshot,
    FactorAttribution,
    FactorPerformanceHistory,
    ForwardOutcome,
    GateCounterfactualRun,
    GateValueEvaluation,
    ModelValidationOrderPlan,
    ModelVersionComparison,
    StockMaster,
    StrategyTimingContract,
)


VERSION = "FORWARD_SHADOW_EVALUATION_V1"
HORIZONS = (1, 3, 5, 10)
FACTOR_FAMILIES = (
    "MOMENTUM", "VOLUME_CAPITAL", "POSITION_TREND", "SENTIMENT_REGIME", "FUNDAMENTAL", "RISK_LIQUIDITY",
)
GATE_SCOPES = {
    "POINT_IN_TIME_DATA": "DATA", "TRADABILITY": "EXECUTION", "MARKET_REGIME": "GLOBAL_MARKET",
    "MARKET_EMOTION": "GLOBAL_MARKET", "HIGH_POSITION_RISK": "STOCK", "LIQUIDITY": "EXECUTION",
    "STRATEGY_CLASSIFICATION": "STRATEGY", "ENTRY_TIMING": "STRATEGY", "ADMISSION": "STRATEGY",
    "CONCENTRATION": "PORTFOLIO", "PRO_RISK": "STOCK", "PREMARKET_RECHECK": "EXECUTION",
}
GATE_ORDER = tuple(GATE_SCOPES)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def sample_status(count: int) -> str:
    if count < 30:
        return "INSUFFICIENT_SAMPLE"
    if count < 100:
        return "PRELIMINARY"
    if count < 500:
        return "USABLE"
    return "STABLE"


def clean_code(value: str) -> str:
    return value.split(".", 1)[0].zfill(6)


@dataclass(frozen=True)
class BackfillOptions:
    as_of_date: date
    source_run_id: str | None = None
    matured_only: bool = True
    execution_policy: str = "NEXT_OPEN"
    buy_slippage_bps: int = 0
    minimum_volume_hands: float = 1000.0
    notional: float = 100_000.0
    no_external_api: bool = True
    no_llm: bool = True
    no_orders: bool = True


class LocalDailyCache:
    def __init__(self, root: Path | str = Path("data/cache/tushare/trade_date")) -> None:
        self.root = Path(root)
        self._cache: dict[tuple[str, date], dict[str, dict]] = {}
        daily = self.root / "daily"
        self.trade_dates = sorted(date.fromisoformat(f"{p.stem[:4]}-{p.stem[4:6]}-{p.stem[6:8]}") for p in daily.glob("*.json"))

    def rows(self, kind: str, trade_date: date) -> dict[str, dict]:
        key = (kind, trade_date)
        if key not in self._cache:
            path = self.root / kind / f"{trade_date:%Y%m%d}.json"
            values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            self._cache[key] = {str(row.get("ts_code", "")): row for row in values}
        return self._cache[key]

    def on_or_after(self, target: date) -> list[date]:
        return [item for item in self.trade_dates if item >= target]


class ForwardShadowBackfillService:
    def __init__(self, session: Session, cache: LocalDailyCache | None = None) -> None:
        self.session = session
        self.cache = cache or LocalDailyCache()

    def run(self, options: BackfillOptions) -> dict[str, Any]:
        if not (options.no_external_api and options.no_llm and options.no_orders):
            raise ValueError("SHADOW_BACKFILL_REQUIRES_OFFLINE_READ_ONLY_MODE")
        contracts = list(self.session.scalars(select(StrategyTimingContract).order_by(StrategyTimingContract.trade_date, StrategyTimingContract.id)))
        stocks = {row.code: row for row in self.session.scalars(select(StockMaster))}
        snapshots = self._snapshot_map()
        v3_by_contract = {row.timing_contract_id: row for row in self.session.scalars(select(AdmissionV3Result))}
        factor_runs: dict[int, str] = {}
        for row in self.session.scalars(select(FactorAttribution)):
            factor_runs.setdefault(row.timing_contract_id, row.run_id)
        maximum_prices: dict[tuple[str, date], float] = {}
        for plan in self.session.scalars(select(ModelValidationOrderPlan).order_by(ModelValidationOrderPlan.id)):
            if plan.max_acceptable_price is not None:
                maximum_prices[(clean_code(plan.stock_code), plan.target_trade_date)] = float(plan.max_acceptable_price)
        created = updated = skipped = 0
        for contract in contracts:
            metadata = self._metadata(contract, snapshots.get(contract.id), v3_by_contract.get(contract.id), factor_runs.get(contract.id))
            if options.source_run_id and metadata["source_run_id"] != options.source_run_id:
                continue
            values = self._evaluate(contract, metadata, stocks.get(contract.stock_code), options,
                                    maximum_prices.get((clean_code(contract.stock_code), contract.order_eligible_at.date())))
            existing = self.session.scalar(select(ForwardOutcome).where(
                ForwardOutcome.source_run_id == metadata["source_run_id"],
                ForwardOutcome.stock_code == contract.stock_code,
                ForwardOutcome.execution_policy == options.execution_policy,
            ))
            if existing is None:
                self.session.add(ForwardOutcome(**values)); created += 1
            else:
                changed = self._mature_existing(existing, values)
                updated += int(changed); skipped += int(not changed)
        self.session.commit()
        evaluation = ForwardEvaluationService(self.session).rebuild(options.as_of_date, options.execution_policy)
        return {"created": created, "updated": updated, "skipped": skipped, **evaluation,
                "external_api_calls": 0, "llm_calls": 0, "orders": 0, "scheduler": False}

    def _snapshot_map(self) -> dict[int, DecisionSnapshot]:
        result: dict[int, DecisionSnapshot] = {}
        for row in self.session.scalars(select(DecisionSnapshot).order_by(DecisionSnapshot.id)):
            contract_id = (row.agent_result_json or {}).get("timing_contract_id")
            if contract_id:
                result[int(contract_id)] = row
        return result

    @staticmethod
    def _metadata(contract, snapshot, v3, factor_run) -> dict[str, Any]:
        agent = snapshot.agent_result_json if snapshot else {}
        if agent.get("route") == "TUSHARE_BASELINE_V1":
            source = f"baseline:{contract.data_snapshot_id}:{contract.trade_date}"
            baseline = "FORMAL_CANDIDATE" if agent.get("formal_candidate") else (snapshot.recommendation or "WATCH")
            return dict(source_run_id=source, route="TUSHARE_BASELINE_V1", baseline_status=baseline,
                        v2_2_status=None, v3_status=None, v3=None)
        if agent.get("route") == "V2.2_V3_SHADOW":
            source = f"v22-v3-reconciliation:{contract.data_snapshot_id}:{contract.trade_date}"
            return dict(source_run_id=source, route="V2.2_V3_SHADOW", baseline_status=None,
                        v2_2_status=agent.get("v2_status"), v3_status=agent.get("v3_status"), v3=v3)
        source = factor_run or (v3.run_id if v3 else f"timing-contract:{contract.id}")
        return dict(source_run_id=source, route="ADMISSION_V3_SHADOW", baseline_status=None,
                    v2_2_status=None, v3_status=(v3.admission_state if v3 else None), v3=v3)

    def _evaluate(self, contract, meta, stock, options: BackfillOptions, max_price: float | None = None) -> dict[str, Any]:
        valid = contract.observation_end_ts <= contract.available_at_ts <= contract.signal_generated_at < contract.order_eligible_at
        entry_date = contract.order_eligible_at.date()
        daily = self.cache.rows("daily", entry_date)
        bar = daily.get(contract.stock_code)
        limit = self.cache.rows("stk_limit", entry_date).get(contract.stock_code, {})
        triggered, binding = self._gates(meta, valid)
        status = "FILLED"
        if not valid:
            status = "DATA_INSUFFICIENT"
        elif entry_date >= options.as_of_date and bar is None:
            status = "DATA_INSUFFICIENT"
        elif not daily:
            status = "DATA_INSUFFICIENT"
        elif bar is None:
            status = "NOT_FILLED_SUSPENDED"
        elif stock and str(stock.status or "").upper() not in {"", "L", "LISTED", "ACTIVE", "正常"}:
            status = "DATA_INSUFFICIENT"
        elif float(bar.get("vol") or 0) < options.minimum_volume_hands:
            status = "NOT_FILLED_LIQUIDITY"
        elif limit.get("up_limit") is not None and float(bar["open"]) >= float(limit["up_limit"]) - 1e-8:
            if float(bar.get("low") or bar["open"]) >= float(limit["up_limit"]) - 1e-8:
                status = "NOT_FILLED_LIMIT_UP"
            else:
                status = "PATH_AMBIGUOUS"
        elif max_price is not None and float(bar["open"]) > max_price:
            status = "NOT_FILLED_PRICE_TOO_HIGH"
        slippage = options.buy_slippage_bps / 10000
        entry = float(bar["open"]) * (1 + slippage) if bar and status == "FILLED" else None
        statuses: dict[str, str] = {}
        result: dict[str, Any] = {}
        path = self.cache.on_or_after(entry_date)
        for horizon in HORIZONS:
            key = f"d{horizon}"
            if not valid:
                statuses[key] = "INVALID_TIMING_CONTRACT"
            elif status != "FILLED":
                statuses[key] = "PENDING" if entry_date >= options.as_of_date and bar is None else "NOT_TRADABLE"
            elif len(path) < horizon or path[horizon - 1] > options.as_of_date:
                statuses[key] = "PENDING"
            else:
                exit_date = path[horizon - 1]
                exit_bar = self.cache.rows("daily", exit_date).get(contract.stock_code)
                if exit_bar is None:
                    statuses[key] = "DATA_MISSING"
                else:
                    statuses[key] = "MATURED"
                    result[f"exit_trade_date_{key}"] = exit_date
                    result[f"exit_price_{key}"] = float(exit_bar["close"])
                    result[f"return_{key}"] = float(exit_bar["close"]) / entry - 1
                    bars = [self.cache.rows("daily", day).get(contract.stock_code) for day in path[:horizon]]
                    bars = [item for item in bars if item]
                    if horizon in (1, 3, 5) and bars:
                        result[f"mae_{key}"] = min(float(item["low"]) / entry - 1 for item in bars)
                        result[f"mfe_{key}"] = max(float(item["high"]) / entry - 1 for item in bars)
        sensitivities = {}
        if bar:
            for bps in (0, 10, 20, 30):
                price = float(bar["open"]) * (1 + bps / 10000)
                sensitivities[f"OPEN_PLUS_{bps}BP"] = {"entry_price": price,
                    **{f"return_d{h}": (float(result[f"exit_price_d{h}"]) / price - 1) if result.get(f"exit_price_d{h}") else None for h in HORIZONS}}
        sensitivities["VWAP_0930_0935"] = {"status": "DATA_MISSING", "reason": "LEGAL_MINUTE_CACHE_UNAVAILABLE"}
        missed_opportunity = avoided_loss = None
        non_fill_quality = None
        if bar and status != "FILLED" and entry_date <= options.as_of_date:
            hypothetical_entry = float(bar["open"]) * (1 + slippage)
            hypothetical_return = float(bar["close"]) / hypothetical_entry - 1
            missed_opportunity = max(hypothetical_return, 0.0)
            avoided_loss = max(-hypothetical_return, 0.0)
            non_fill_quality = "MISSED_GAIN" if hypothetical_return > 0 else ("AVOIDED_LOSS" if hypothetical_return < 0 else "NEUTRAL")
        data_status = "MATURED" if any(v == "MATURED" for v in statuses.values()) else next(iter(set(statuses.values())))
        code = clean_code(contract.stock_code)
        return dict(source_run_id=meta["source_run_id"], route=meta["route"], trade_date=contract.trade_date,
                    stock_code=contract.stock_code, stock_name=(stock.name if stock else None), industry=(stock.industry if stock else None),
                    timing_contract_id=contract.id, data_snapshot_id=contract.data_snapshot_id,
                    universe_snapshot_id=contract.universe_snapshot_id, baseline_status=meta["baseline_status"],
                    v2_2_status=meta["v2_2_status"], v3_status=meta["v3_status"], factor_version=contract.feature_version,
                    strategy_version="STRATEGY_PROBABILITY_SHADOW_V1", gate_version="GATE_VALUE_V1",
                    execution_policy=options.execution_policy, entry_trade_date=entry_date, entry_price=entry,
                    entry_status=status, benchmark_return=None, industry_return=None, data_status=data_status,
                    horizon_status_json=statuses, sensitivity_json=sensitivities,
                    execution_details_json={"stock_code_text": code, "slippage_bps": options.buy_slippage_bps,
                        "lot_size": 100, "quantity": (math.floor(options.notional / entry / 100) * 100 if entry else 0),
                        "max_acceptable_price": max_price, "timing_mode": "EOD_T_TO_NEXT_OPEN"},
                    missed_opportunity=missed_opportunity, avoided_loss=avoided_loss, non_fill_quality=non_fill_quality,
                    all_triggered_gates=triggered, binding_gate=binding,
                    co_binding_gates=[x for x in triggered if x != binding], evaluation_order=list(GATE_ORDER),
                    unique_block_reason=(binding if len(triggered) == 1 else None), joint_block_reason=(" + ".join(triggered) if len(triggered) > 1 else None),
                    input_hash=_hash([meta["source_run_id"], contract.id, options.execution_policy, options.buy_slippage_bps, options.as_of_date]),
                    version=VERSION, **result)

    @staticmethod
    def _gates(meta: dict, valid: bool) -> tuple[list[str], str | None]:
        gates: list[str] = []
        if not valid:
            gates.append("POINT_IN_TIME_DATA")
        v3 = meta.get("v3")
        penalties = v3.risk_penalties if v3 else {}
        if "MARKET_RED" in penalties or meta.get("v2_2_status") == "SHADOW_BLOCK":
            gates.append("MARKET_REGIME")
        if "HIGH_POSITION_RISK" in penalties:
            gates.append("HIGH_POSITION_RISK")
        if meta.get("v2_2_status") == "SHADOW_BLOCK":
            gates.append("ADMISSION")
        return gates, (gates[0] if gates else None)

    @staticmethod
    def _mature_existing(existing: ForwardOutcome, new: dict[str, Any]) -> bool:
        changed = False
        if existing.entry_status == "FILLED" and new.get("entry_status") in {
            "NOT_FILLED_LIMIT_UP", "NOT_FILLED_PRICE_TOO_HIGH", "NOT_FILLED_LIQUIDITY",
            "CANCELLED_BY_PREMARKET", "PATH_AMBIGUOUS",
        }:
            existing.entry_status = new["entry_status"]
            existing.entry_price = None
            for horizon in HORIZONS:
                suffix = f"d{horizon}"
                for prefix in ("exit_trade_date_", "exit_price_", "return_", "mae_", "mfe_"):
                    field = f"{prefix}{suffix}"
                    if hasattr(existing, field):
                        setattr(existing, field, None)
            existing.horizon_status_json = {f"d{h}": "NOT_TRADABLE" for h in HORIZONS}
            existing.data_status = "NOT_TRADABLE"
            existing.execution_details_json = new["execution_details_json"]
            existing.missed_opportunity = new.get("missed_opportunity")
            existing.avoided_loss = new.get("avoided_loss")
            existing.non_fill_quality = new.get("non_fill_quality")
            return True
        for horizon in HORIZONS:
            suffix = f"d{horizon}"
            if getattr(existing, f"return_{suffix}") is None and new.get(f"return_{suffix}") is not None:
                for prefix in ("exit_trade_date_", "exit_price_", "return_", "mae_", "mfe_"):
                    field = f"{prefix}{suffix}"
                    if hasattr(existing, field) and field in new:
                        setattr(existing, field, new[field])
                changed = True
        merged = dict(existing.horizon_status_json or {})
        for key, status in new["horizon_status_json"].items():
            if merged.get(key) != "MATURED":
                merged[key] = status
        if merged != existing.horizon_status_json:
            existing.horizon_status_json = merged; changed = True
        if existing.entry_price is None and new.get("entry_price") is not None:
            existing.entry_price = new["entry_price"]; existing.entry_status = new["entry_status"]; changed = True
        if existing.entry_status != "FILLED" and existing.non_fill_quality is None and new.get("non_fill_quality") is not None:
            existing.missed_opportunity = new.get("missed_opportunity")
            existing.avoided_loss = new.get("avoided_loss")
            existing.non_fill_quality = new.get("non_fill_quality")
            changed = True
        existing.data_status = "MATURED" if "MATURED" in merged.values() else new["data_status"]
        return changed


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return statistics.fmean(items) if items else None


def _profit_factor(values: list[float]) -> float | None:
    gain = sum(x for x in values if x > 0); loss = -sum(x for x in values if x < 0)
    return (gain / loss) if loss else (None if gain == 0 else float("inf"))


class ForwardEvaluationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def rebuild(self, as_of: date, policy: str) -> dict[str, Any]:
        rows = list(self.session.scalars(select(ForwardOutcome).where(ForwardOutcome.execution_policy == policy,
                                                                      ForwardOutcome.entry_trade_date <= as_of)))
        run_id = f"forward-shadow:{as_of}:{policy}"
        comparisons = self._comparisons(run_id, as_of, rows)
        gates = self._gates(run_id, as_of, rows)
        factors = self._factors(as_of, rows)
        self.session.commit()
        matured = {f"d{h}": sum(1 for r in rows if (r.horizon_status_json or {}).get(f"d{h}") == "MATURED") for h in HORIZONS}
        return {"run_id": run_id, "outcome_count": len(rows), "matured": matured,
                "pending": sum(1 for r in rows for v in (r.horizon_status_json or {}).values() if v == "PENDING"),
                "tradable": sum(r.entry_status == "FILLED" for r in rows),
                "non_tradable": sum(r.entry_status != "FILLED" and r.entry_status != "DATA_INSUFFICIENT" for r in rows),
                "comparison_rows": comparisons, "gate_rows": gates, "factor_rows": factors,
                "sample_status": sample_status(matured["d3"]), "fair_ab_matured_count": 0,
                "fair_ab_sample_status": "INSUFFICIENT_SAMPLE", "promotion_recommendation": "KEEP_V22_V3_SHADOW"}

    def _comparisons(self, run_id: str, as_of: date, rows: list[ForwardOutcome]) -> int:
        count = 0
        for horizon in HORIZONS:
            attr = f"return_d{horizon}"
            groups: dict[tuple[str, str], list[ForwardOutcome]] = defaultdict(list)
            for row in rows:
                segment = row.baseline_status or row.v2_2_status or row.v3_status or "UNSEGMENTED"
                groups[(row.route, segment)].append(row)
            for (route, segment), group in groups.items():
                returns = [float(getattr(x, attr)) for x in group if getattr(x, attr) is not None and x.entry_status == "FILLED"]
                maes = [float(getattr(x, f"mae_d{horizon}")) for x in group if horizon <= 5 and getattr(x, f"mae_d{horizon}") is not None]
                mfes = [float(getattr(x, f"mfe_d{horizon}")) for x in group if horizon <= 5 and getattr(x, f"mfe_d{horizon}") is not None]
                material = [as_of, route, segment, horizon, [x.input_hash for x in group]]
                input_hash = _hash(material)
                if self.session.scalar(select(ModelVersionComparison).where(ModelVersionComparison.input_hash == input_hash)):
                    continue
                self.session.add(ModelVersionComparison(run_id=run_id, as_of_date=as_of, model_route=route, segment=segment,
                    horizon=f"D{horizon}", sample_count=len(group), fill_count=len(returns),
                    positive_rate=(sum(x > 0 for x in returns) / len(returns) if returns else None), average_return=_mean(returns),
                    median_return=(statistics.median(returns) if returns else None), average_mae=_mean(maes), average_mfe=_mean(mfes),
                    profit_factor=_profit_factor(returns), maximum_loss=(min(returns) if returns else None), sample_status=sample_status(len(returns)),
                    fair_sample=False, details_json={"fair_ab_matured_count": 0, "reason": "ROUTE_SIGNAL_TIMES_HAVE_DIFFERENT_ELIGIBLE_DATES"},
                    input_hash=input_hash, version=VERSION)); count += 1
        count += self._v3_stratifications(run_id, as_of, rows)
        return count

    def _v3_stratifications(self, run_id: str, as_of: date, rows: list[ForwardOutcome]) -> int:
        v3_map = {x.timing_contract_id: x for x in self.session.scalars(select(AdmissionV3Result)) if x.admission_state == "REVIEW"}
        pairs = [(row, v3_map[row.timing_contract_id]) for row in rows if row.timing_contract_id in v3_map]
        metrics = {
            "expected_value_score": lambda x: float(x.expected_value_score or 0),
            "risk_adjusted_opportunity_score": lambda x: float(x.risk_adjusted_opportunity_score or 0),
            "opportunity_score": lambda x: float(x.opportunity_score or 0),
            "risk_penalty": lambda x: -sum(float(v.get("score_penalty", 0)) for v in (x.risk_penalties or {}).values()),
            "strategy_confidence": lambda x: max((float(v) for k, v in (x.strategy_probability or {}).items() if k != "OPEN_SET"), default=0),
            "open_set_score": lambda x: -float((x.strategy_probability or {}).get("OPEN_SET", 0)),
            "entry_timing": lambda x: float((x.opportunity_components or {}).get("entry_timing", 0)),
            "admission_score": lambda x: float(x.final_score or 0),
            "baseline_quant_score": lambda x: float(x.base_score or 0),
        }
        count = 0
        for metric, getter in metrics.items():
            ranked = sorted(pairs, key=lambda pair: getter(pair[1]), reverse=True)
            n = len(ranked); top10 = max(1, math.ceil(n * .10)); top20 = max(1, math.ceil(n * .20))
            groups = {
                "TOP10": ranked[:top10], "TOP20": ranked[:top20],
                "MIDDLE60": ranked[top20:max(top20, math.floor(n * .80))], "BOTTOM20": ranked[math.floor(n * .80):],
            }
            for group_name, group in groups.items():
                for horizon in (1, 3, 5):
                    attr = f"return_d{horizon}"
                    returns = [float(getattr(row, attr)) for row, _ in group if getattr(row, attr) is not None and row.entry_status == "FILLED"]
                    mae_attr = f"mae_d{horizon}"; mfe_attr = f"mfe_d{horizon}"
                    maes = [float(getattr(row, mae_attr)) for row, _ in group if getattr(row, mae_attr, None) is not None]
                    mfes = [float(getattr(row, mfe_attr)) for row, _ in group if getattr(row, mfe_attr, None) is not None]
                    ih = _hash([as_of, "V3_REVIEW", metric, group_name, horizon, [row.input_hash for row, _ in group]])
                    if self.session.scalar(select(ModelVersionComparison).where(ModelVersionComparison.input_hash == ih)):
                        continue
                    self.session.add(ModelVersionComparison(run_id=run_id, as_of_date=as_of,
                        model_route="V3_REVIEW_STRATIFICATION", segment=f"{metric}:{group_name}", horizon=f"D{horizon}",
                        sample_count=len(group), fill_count=len(returns), positive_rate=(sum(x > 0 for x in returns)/len(returns) if returns else None),
                        average_return=_mean(returns), median_return=(statistics.median(returns) if returns else None),
                        average_mae=_mean(maes), average_mfe=_mean(mfes), profit_factor=_profit_factor(returns),
                        maximum_loss=(min(returns) if returns else None), sample_status=sample_status(len(returns)), fair_sample=True,
                        details_json={"all_original_states_preserved": "REVIEW", "ranking_metric": metric, "overlapping_top10_top20": True},
                        input_hash=ih, version=VERSION)); count += 1
        # Strategy probability performance is evaluated by the dominant probability, without changing the classifier.
        strategies: dict[str, list[tuple[ForwardOutcome, AdmissionV3Result]]] = defaultdict(list)
        for row, v3 in pairs:
            probs = v3.strategy_probability or {"OPEN_SET": 1.0}
            strategies[max(probs, key=probs.get)].append((row, v3))
        for strategy, group in strategies.items():
            for horizon in (1, 3, 5):
                returns = [float(getattr(row, f"return_d{horizon}")) for row, _ in group if getattr(row, f"return_d{horizon}") is not None]
                ih = _hash([as_of, "STRATEGY_PROBABILITY", strategy, horizon, [row.input_hash for row, _ in group]])
                if self.session.scalar(select(ModelVersionComparison).where(ModelVersionComparison.input_hash == ih)):
                    continue
                self.session.add(ModelVersionComparison(run_id=run_id, as_of_date=as_of, model_route="STRATEGY_PROBABILITY",
                    segment=strategy, horizon=f"D{horizon}", sample_count=len(group), fill_count=len(returns),
                    positive_rate=(sum(x > 0 for x in returns)/len(returns) if returns else None), average_return=_mean(returns),
                    median_return=(statistics.median(returns) if returns else None), average_mae=None, average_mfe=None,
                    profit_factor=_profit_factor(returns), maximum_loss=(min(returns) if returns else None),
                    sample_status=sample_status(len(returns)), fair_sample=True,
                    details_json={"probability_sum_contract": 1, "classifier_unchanged": True}, input_hash=ih, version=VERSION)); count += 1
        return count

    def _gates(self, run_id: str, as_of: date, rows: list[ForwardOutcome]) -> int:
        count = 0
        matured = [r for r in rows if r.return_d1 is not None]
        v3_scores = {x.timing_contract_id: float(x.final_score) for x in self.session.scalars(select(AdmissionV3Result))}

        def select_portfolio(candidates: list[ForwardOutcome]) -> list[ForwardOutcome]:
            ranked = sorted(candidates, key=lambda r: (v3_scores.get(r.timing_contract_id, 0.0), r.stock_code), reverse=True)
            chosen: list[ForwardOutcome] = []; industry_counts: dict[str, int] = defaultdict(int)
            for row in ranked:
                industry = row.industry or "UNKNOWN"
                if industry_counts[industry] >= 4:
                    continue
                chosen.append(row); industry_counts[industry] += 1
                if len(chosen) == 20:
                    break
            return chosen

        original_pool = [r for r in matured if not (r.all_triggered_gates or [])]
        original_portfolio = select_portfolio(original_pool)
        original_returns = [float(r.return_d1) for r in original_portfolio]
        for gate, scope in GATE_SCOPES.items():
            reached = rows if scope != "GLOBAL_MARKET" else list({(r.trade_date, r.data_snapshot_id): r for r in rows}.values())
            triggered = [r for r in rows if gate in (r.all_triggered_gates or [])]
            global_trigger_count = len({(r.trade_date, r.data_snapshot_id) for r in triggered})
            # Binding attribution prevents the same loss from being credited to every overlapping gate.
            attributable = [r for r in triggered if r.binding_gate == gate]
            utilities = [float(r.return_d1) - max(0.0, -float(r.mae_d1 or 0)) for r in attributable if r.return_d1 is not None]
            avoided = sum(max(-x, 0) for x in utilities); missed = sum(max(x, 0) for x in utilities)
            restored = [r for r in attributable if r.return_d1 is not None and not (r.co_binding_gates or [])]
            disabled_portfolio = select_portfolio(original_pool + restored)
            disabled_returns = [float(r.return_d1) for r in disabled_portfolio]
            delta_net = ((_mean(disabled_returns) or 0) - (_mean(original_returns) or 0)) if disabled_returns or original_returns else None
            delta_win = ((sum(x > 0 for x in disabled_returns) / len(disabled_returns) if disabled_returns else 0)
                         - (sum(x > 0 for x in original_returns) / len(original_returns) if original_returns else 0)) if disabled_returns or original_returns else None
            pf_disabled = _profit_factor(disabled_returns); pf_original = _profit_factor(original_returns)
            delta_pf = (pf_disabled - pf_original) if pf_disabled is not None and pf_original is not None and math.isfinite(pf_disabled) and math.isfinite(pf_original) else None
            top_flip = len({r.timing_contract_id for r in original_portfolio} ^ {r.timing_contract_id for r in disabled_portfolio})
            delta_drawdown = ((min(disabled_returns) if disabled_returns else 0) - (min(original_returns) if original_returns else 0)) if disabled_returns or original_returns else None
            def cvar(values: list[float]) -> float | None:
                if not values: return None
                tail = sorted(values)[:max(1, math.ceil(len(values) * .05))]
                return _mean(tail)
            old_cvar, new_cvar = cvar(original_returns), cvar(disabled_returns)
            delta_cvar = (new_cvar - old_cvar) if old_cvar is not None and new_cvar is not None else None
            ih = _hash([as_of, gate, "D1", [r.input_hash for r in matured]])
            if not self.session.scalar(select(GateValueEvaluation).where(GateValueEvaluation.input_hash == ih)):
                self.session.add(GateValueEvaluation(run_id=run_id, as_of_date=as_of, gate_name=gate, gate_scope=scope,
                    reached_count=len(reached), triggered_count=(global_trigger_count if scope == "GLOBAL_MARKET" else len(triggered)),
                    blocked_count=len(triggered), unique_blocked_count=sum(len(r.all_triggered_gates or []) == 1 for r in triggered),
                    co_blocked_count=sum(len(r.all_triggered_gates or []) > 1 for r in triggered), binding_count=sum(r.binding_gate == gate for r in triggered),
                    pass_flip_count=len(restored), top20_flip_count=top_flip, mean_rank_delta=None, mean_score_delta=None, distance_to_threshold=None,
                    avoided_loss=avoided if utilities else None, missed_gain=missed if utilities else None,
                    local_net_gate_value=(avoided-missed if utilities else None), portfolio_marginal_value=(-delta_net if delta_net is not None else None),
                    false_negative_rate=(sum(x > 0 for x in utilities)/len(utilities) if utilities else None),
                    reject_precision=(sum(x <= 0 for x in utilities)/len(utilities) if utilities else None),
                    evaluation_horizon="D1", sample_status=sample_status(len(utilities)), overlap_json={"shapley_ready": True},
                    input_hash=ih, version=VERSION)); count += 1
            cf_hash = _hash([ih, "leave-one-gate-out"])
            if not self.session.scalar(select(GateCounterfactualRun).where(GateCounterfactualRun.input_hash == cf_hash)):
                self.session.add(GateCounterfactualRun(run_id=run_id, as_of_date=as_of, disabled_gate=gate, evaluation_horizon="D1",
                    delta_net_return=delta_net, delta_win_rate=delta_win, delta_profit_factor=delta_pf, delta_max_drawdown=delta_drawdown, delta_cvar=delta_cvar,
                    delta_candidate_count=len(restored), delta_turnover=(top_flip / 20 if original_portfolio or disabled_portfolio else None),
                    delta_top20_membership=top_flip, avoided_loss=avoided if utilities else None,
                    missed_gain=missed if utilities else None, net_gate_value=(avoided-missed if utilities else None),
                    replay_status=("INSUFFICIENT_SAMPLE" if len(restored) < 30 else "COMPLETED"),
                    shapley_ready_json={"coalition_keys": [], "gate_scope": scope, "global_gate_replayed_once": scope == "GLOBAL_MARKET",
                        "portfolio_rule": "TOP20_INDUSTRY_CAP_4", "other_gates_preserved": True},
                    input_hash=cf_hash, version=VERSION))
        return count

    def _factors(self, as_of: date, rows: list[ForwardOutcome]) -> int:
        outcomes = {r.timing_contract_id: r for r in rows if r.return_d1 is not None}
        grouped: dict[str, list[tuple[FactorAttribution, ForwardOutcome]]] = defaultdict(list)
        deduplicated: dict[tuple[int, str], FactorAttribution] = {}
        for factor in self.session.scalars(select(FactorAttribution)):
            if factor.timing_contract_id in outcomes:
                deduplicated[(factor.timing_contract_id, factor.factor_family)] = factor
        for factor in deduplicated.values():
            grouped[factor.factor_family].append((factor, outcomes[factor.timing_contract_id]))
        count = 0
        for family in FACTOR_FAMILIES:
            pairs = grouped.get(family, [])
            contributions = [float(f.score_contribution) for f, _ in pairs]
            returns = [float(o.return_d1) for _, o in pairs]
            ih = _hash([as_of, family, [(f.id, o.input_hash) for f, o in pairs]])
            if self.session.scalar(select(FactorPerformanceHistory).where(FactorPerformanceHistory.input_hash == ih,
                                                                          FactorPerformanceHistory.factor_family == family)):
                continue
            corr = None
            if len(pairs) > 1 and statistics.pstdev(contributions) and statistics.pstdev(returns):
                corr = statistics.correlation(contributions, returns)
            top = sorted(zip(contributions, returns), reverse=True)
            q = max(1, len(top)//5) if top else 0
            self.session.add(FactorPerformanceHistory(factor_family=family, sample_count=len(pairs),
                win_rate=(sum(x > 0 for x in returns)/len(returns) if returns else None), avg_return_d1=_mean(returns),
                avg_return_d3=None, avg_return_d5=None,
                avg_drawdown=_mean([float(o.mae_d1) for _, o in pairs if o.mae_d1 is not None]),
                positive_contribution_rate=(sum(x > 0 for x in contributions)/len(contributions) if contributions else None),
                negative_contribution_rate=(sum(x < 0 for x in contributions)/len(contributions) if contributions else None),
                positive_contribution_count=sum(x > 0 for x in contributions), negative_contribution_count=sum(x < 0 for x in contributions),
                mean_contribution=_mean(contributions), median_contribution=(statistics.median(contributions) if contributions else None),
                contribution_return_correlation=corr, contribution_rank_ic=corr,
                top_contribution_return=(_mean([x[1] for x in top[:q]]) if q else None),
                bottom_contribution_return=(_mean([x[1] for x in top[-q:]]) if q else None),
                contribution_hit_rate=(sum((c >= 0) == (r >= 0) for c, r in zip(contributions, returns))/len(pairs) if pairs else None),
                average_mfe=_mean([float(o.mfe_d1) for _, o in pairs if o.mfe_d1 is not None]), profit_factor=_profit_factor(returns),
                pass_contribution_json={"metric": "gate_contribution", "mean": _mean([float(f.gate_contribution) for f, _ in pairs])},
                rank_contribution_json={"metric": "rank_contribution", "mean": _mean([float(f.rank_contribution) for f, _ in pairs])},
                period=f"FORWARD_TO_{as_of}", period_start=(min((o.trade_date for _, o in pairs), default=as_of)), period_end=as_of,
                input_hash=ih, version=VERSION)); count += 1
        return count
