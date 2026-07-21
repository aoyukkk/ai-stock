from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any, Iterable


@dataclass(frozen=True)
class MiddayScore:
    feature_scope: str
    overlay_score: float | None
    delta: float
    enhanced_score: float
    components: dict[str, float]
    data_quality: dict[str, Any]
    hard_gate_status: str
    candidate_action: str
    held_action: str | None
    prices: dict[str, float | None]
    reasons: list[str]
    risks: list[str]


class MiddayScoringEngine:
    """Deterministic shadow scoring; it never changes the official Quant score."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        overlay = config.get("overlay", {})
        self.scale = float(overlay.get("scale", 0.12))
        self.snapshot_cap = float(overlay.get("snapshot_delta_cap", 2.0))
        self.minute_cap = float(overlay.get("minute_delta_cap", 6.0))
        self.minimum_completeness = float(overlay.get("minimum_minute_completeness", 0.95))

    def score(
        self,
        item: dict[str, Any],
        snapshot: Any | None,
        minute_rows: Iterable[Any] | None,
        *,
        index_return: float | None,
    ) -> MiddayScore:
        base = float(item["base_quant_score"])
        snap = self._snapshot_components(snapshot, index_return)
        minute, completeness = self._minute_components(list(minute_rows or []))
        has_snapshot = snapshot is not None and _positive(_value(snapshot, "latest"))
        full_minute = has_snapshot and completeness >= self.minimum_completeness
        if full_minute:
            feature_scope = "MORNING_FULL_MINUTE"
            components = {**snap, **minute}
            overlay_score = (
                components["relative_strength"] * 0.20
                + components["close_quality"] * 0.15
                + components["market_regime_fit"] * 0.10
                + components["morning_strength"] * 0.25
                + components["intraday_stability"] * 0.15
                + components["liquidity_confirmation"] * 0.15
            )
            cap = self.minute_cap
        elif has_snapshot:
            feature_scope = "MORNING_SNAPSHOT_ONLY"
            components = snap
            overlay_score = (
                snap["relative_strength"] * 0.40
                + snap["close_quality"] * 0.35
                + snap["market_regime_fit"] * 0.25
            )
            cap = self.snapshot_cap
        else:
            feature_scope = "BASELINE_ONLY"
            components = {}
            overlay_score = None
            cap = 0.0
        delta = 0.0 if overlay_score is None else _clip((overlay_score - 50.0) * self.scale, -cap, cap)
        enhanced = _clip(base + delta, 0.0, 100.0)
        hard_gate, risks = self._hard_gate(snapshot, feature_scope)
        candidate_action = self._candidate_action(enhanced, hard_gate)
        held_action = self._held_action(enhanced, hard_gate, item) if item.get("position_status") == "HELD" else None
        reasons = [f"上一交易日量化基线排名 {item['base_quant_rank']}"]
        if overlay_score is not None:
            reasons.append(f"午间影子增强分 {overlay_score:.2f}，增量 {delta:+.2f}")
        else:
            risks.append("午间行情不可用，仅保留上一交易日基线")
        return MiddayScore(
            feature_scope=feature_scope,
            overlay_score=overlay_score,
            delta=delta,
            enhanced_score=enhanced,
            components=components,
            data_quality={"minute_completeness": completeness, "snapshot_available": has_snapshot},
            hard_gate_status=hard_gate,
            candidate_action=candidate_action,
            held_action=held_action,
            prices=self._prices(snapshot, hard_gate),
            reasons=reasons,
            risks=risks,
        )

    def _snapshot_components(self, row: Any | None, index_return: float | None) -> dict[str, float]:
        latest = _value(row, "latest")
        pre_close = _value(row, "pre_close")
        open_price = _value(row, "open")
        high = _value(row, "high")
        low = _value(row, "low")
        stock_return = _return(latest, pre_close or open_price)
        benchmark = index_return or 0.0
        relative = _clip(50.0 + (stock_return - benchmark) * 500.0, 0.0, 100.0)
        close_quality = 50.0 if high <= low else _clip((latest - low) / (high - low) * 100.0, 0.0, 100.0)
        regime = 70.0 if stock_return == 0 or benchmark == 0 or stock_return * benchmark >= 0 else 35.0
        return {"relative_strength": relative, "close_quality": close_quality, "market_regime_fit": regime}

    def _minute_components(self, rows: list[Any]) -> tuple[dict[str, float], float]:
        closes = [_value(row, "close") for row in rows if _positive(_value(row, "close"))]
        expected = 61
        completeness = min(1.0, len(closes) / expected)
        if len(closes) < 2:
            return {"morning_strength": 50.0, "intraday_stability": 0.0, "liquidity_confirmation": 0.0}, completeness
        returns = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes))]
        strength = _clip(50.0 + _return(closes[-1], closes[0]) * 1000.0, 0.0, 100.0)
        stability = _clip(100.0 - pstdev(returns) * 10000.0, 0.0, 100.0)
        volumes = [_value(row, "volume") for row in rows]
        midpoint = max(1, len(volumes) // 2)
        earlier = mean(volumes[:midpoint]) if volumes[:midpoint] else 0.0
        later = mean(volumes[midpoint:]) if volumes[midpoint:] else 0.0
        liquidity = 50.0 if earlier <= 0 else _clip(50.0 + (later / earlier - 1.0) * 25.0, 0.0, 100.0)
        return {"morning_strength": strength, "intraday_stability": stability, "liquidity_confirmation": liquidity}, completeness

    def _hard_gate(self, row: Any | None, scope: str) -> tuple[str, list[str]]:
        if scope == "BASELINE_ONLY":
            return "DATA_INSUFFICIENT", ["午间实时数据不足"]
        latest = _value(row, "latest")
        limit_up = _value(row, "limit_up")
        limit_down = _value(row, "limit_down")
        if limit_up > 0 and latest >= limit_up * 0.997:
            return "LIMIT_UP_NEAR", ["接近涨停，不宜追高"]
        if limit_down > 0 and latest <= limit_down * 1.003:
            return "LIMIT_DOWN_NEAR", ["接近跌停，流动性风险较高"]
        return "PASS", []

    @staticmethod
    def _candidate_action(score: float, hard_gate: str) -> str:
        if hard_gate == "LIMIT_UP_NEAR":
            return "DO_NOT_CHASE"
        if hard_gate == "DATA_INSUFFICIENT":
            return "DATA_INSUFFICIENT"
        if hard_gate != "PASS":
            return "MANUAL_REVIEW"
        if score >= 75:
            return "AFTERNOON_PREPARE_ENTRY"
        if score >= 65:
            return "WAIT_PULLBACK"
        if score >= 55:
            return "KEEP_WATCH"
        return "REMOVE_FROM_POOL"

    @staticmethod
    def _held_action(score: float, hard_gate: str, item: dict[str, Any] | None = None) -> str:
        positions = (item or {}).get("positions") or []
        if any(int(_value(position, "available_quantity")) <= 0 for position in positions):
            return "T_PLUS_ONE_LOCKED"
        if hard_gate == "DATA_INSUFFICIENT":
            return "DATA_INSUFFICIENT"
        if hard_gate != "PASS":
            return "MANUAL_REVIEW"
        if score >= 70:
            return "CONTINUE_HOLD"
        if score >= 60:
            return "HOLD_WITH_TIGHT_STOP"
        if score >= 50:
            return "REDUCE_IF_WEAKENS"
        return "EXIT_IF_TRIGGERED"

    @staticmethod
    def _prices(row: Any | None, hard_gate: str) -> dict[str, float | None]:
        latest = _value(row, "latest")
        high = _value(row, "high")
        low = _value(row, "low")
        limit_up = _value(row, "limit_up")
        if latest <= 0 or hard_gate not in {"PASS", "LIMIT_UP_NEAR"}:
            return {key: None for key in ("recommended_price", "max_acceptable_price", "stop_loss", "take_profit_1", "take_profit_2")}
        risk = max(latest * 0.01, max(0.0, high - low) * 0.5)
        maximum = min(limit_up, latest + risk * 0.25) if limit_up > 0 else latest + risk * 0.25
        return {
            "recommended_price": round(latest - risk * 0.25, 2),
            "max_acceptable_price": round(maximum, 2),
            "stop_loss": round(max(0.01, latest - risk * 1.5), 2),
            "take_profit_1": round(latest + risk * 1.5, 2),
            "take_profit_2": round(latest + risk * 2.5, 2),
        }


def _value(row: Any | None, field: str) -> float:
    if row is None:
        return 0.0
    value = row.get(field) if isinstance(row, dict) else getattr(row, field, None)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _return(current: float, base: float) -> float:
    return current / base - 1.0 if current > 0 and base > 0 else 0.0


def _positive(value: float) -> bool:
    return value > 0


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
