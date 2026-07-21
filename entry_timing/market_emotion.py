from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MarketEmotionAssessment:
    breadth_health: float | None
    limit_structure_health: float | None
    break_board_health: float | None
    median_return_health: float | None
    turnover_health: float | None
    tail_risk_health: float | None
    market_emotion_score: float | None
    emotion_state: str
    component_coverage: float
    missing_components: list[str]
    input_hash: str
    version: str


class MarketEmotionEngine:
    WEIGHTS = {
        "breadth_health": 0.25, "limit_structure_health": 0.20,
        "break_board_health": 0.15, "median_return_health": 0.15,
        "turnover_health": 0.15, "tail_risk_health": 0.10,
    }

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.version = str(config.get("market_emotion_version") or "market_emotion_gate_v1")

    def evaluate(self, snapshot: dict[str, Any]) -> MarketEmotionAssessment:
        breadth = snapshot.get("breadth") or {}
        limits = snapshot.get("limit_structure") or {}
        turnover = snapshot.get("turnover") or {}
        values = {
            "breadth_health": self._breadth(breadth),
            "limit_structure_health": self._limits(limits),
            "break_board_health": self._break_board(limits),
            "median_return_health": self._median(breadth),
            "turnover_health": self._turnover(turnover, breadth),
            "tail_risk_health": self._tail(breadth, limits),
        }
        available = {key: value for key, value in values.items() if value is not None}
        weight = sum(self.WEIGHTS[key] for key in available)
        score = sum(value * self.WEIGHTS[key] for key, value in available.items()) / weight if weight else None
        coverage = weight / sum(self.WEIGHTS.values())
        cfg = self.config["market_emotion"]
        if score is None or coverage < float(cfg["minimum_component_coverage"]):
            state = "DATA_INSUFFICIENT"
        elif score >= float(cfg["green_minimum"]):
            state = "GREEN"
        elif score >= float(cfg["yellow_minimum"]):
            state = "YELLOW"
        else:
            state = "RED"
        input_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
        return MarketEmotionAssessment(
            **{key: round(value, 4) if value is not None else None for key, value in values.items()},
            market_emotion_score=round(score, 4) if score is not None else None,
            emotion_state=state, component_coverage=round(coverage, 4),
            missing_components=[key for key, value in values.items() if value is None],
            input_hash=input_hash, version=self.version,
        )

    @staticmethod
    def _breadth(data: dict[str, Any]) -> float | None:
        ratio = _number(data.get("advancing_ratio"))
        if ratio is None: return None
        if ratio >= .70: return 95
        if ratio >= .55: return 78
        if ratio >= .45: return 58
        if ratio >= .30: return 38
        return 12

    @staticmethod
    def _limits(data: dict[str, Any]) -> float | None:
        up, down = _number(data.get("limit_up_count")), _number(data.get("limit_down_count"))
        if up is None or down is None: return None
        ratio = up / max(1, up + down)
        base = 15 + ratio * 80
        if down >= 150: base -= 20
        elif down >= 80: base -= 10
        return _clamp(base)

    @staticmethod
    def _break_board(data: dict[str, Any]) -> float | None:
        ratio = _number(data.get("failed_limit_up_ratio"))
        if ratio is None: return None
        ratio = max(0, min(1, ratio))
        if ratio <= .15: return 90
        if ratio <= .25: return 75
        if ratio <= .40: return 52
        if ratio <= .60: return 28
        return 10

    @staticmethod
    def _median(data: dict[str, Any]) -> float | None:
        value = _number(data.get("median_return"))
        if value is None: return None
        if value >= .02: return 95
        if value >= .005: return 75
        if value >= -.005: return 55
        if value >= -.02: return 35
        return 10

    @staticmethod
    def _turnover(data: dict[str, Any], breadth: dict[str, Any]) -> float | None:
        relative = _number(data.get("relative_to_5d"))
        median = _number(breadth.get("median_return"))
        if relative is None: return None
        if relative > 1.35 and median is not None and median < -.01: return 15
        if 1.00 <= relative <= 1.25: return 85
        if .85 <= relative < 1.00: return 68
        if .70 <= relative < .85: return 42
        if relative < .70: return 18
        return 58

    @staticmethod
    def _tail(breadth: dict[str, Any], limits: dict[str, Any]) -> float | None:
        valid = _number(breadth.get("valid_count"))
        below3, below5 = _number(breadth.get("below_3_count")), _number(breadth.get("below_5_count"))
        down = _number(limits.get("limit_down_count"))
        if not valid or below3 is None or below5 is None: return None
        pressure = below3 / valid * .45 + below5 / valid * .45 + min(1, (down or 0) / 200) * .10
        return _clamp(100 * (1 - pressure))


class StrategyMarketEmotionGate:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def evaluate(self, strategy_id: str, emotion_state: str, *, market_regime: str, strategy_fit: float, pullback_quality: float, sector_score: float, reversal_confirmation: bool) -> tuple[str, list[str], float]:
        if emotion_state == "DATA_INSUFFICIENT":
            return "BLOCK", ["MARKET_EMOTION_DATA_INSUFFICIENT"], 0
        if strategy_id == "UNCLASSIFIED":
            return "REVIEW", ["UNCLASSIFIED_CANNOT_PASS"], 0
        if emotion_state == "GREEN":
            if strategy_id == "OVERSOLD_REBOUND": return "REVIEW", ["OVERSOLD_GREEN_REVIEW_ONLY"], 0
            return "PASS", [], 0
        if emotion_state == "YELLOW":
            if strategy_id == "TREND_BREAKOUT": return "PASS", ["YELLOW_BREAKOUT_THRESHOLD_RAISED"], float(self.config["market_gate"]["yellow_breakout_increment"])
            if strategy_id == "STRONG_PULLBACK":
                ok = strategy_fit >= float(self.config["market_gate"]["yellow_pullback_fit_minimum"]) and pullback_quality >= float(self.config["market_gate"]["yellow_pullback_quality_minimum"])
                return ("PASS", [], 0) if ok else ("REVIEW", ["YELLOW_PULLBACK_QUALITY_REVIEW"], 0)
            if strategy_id == "SECTOR_RESONANCE":
                return ("PASS", [], 0) if sector_score >= float(self.config["market_gate"]["yellow_sector_minimum"]) else ("REVIEW", ["YELLOW_SECTOR_SCORE_REVIEW"], 0)
            allowed = market_regime in set(self.config["strategies"]["oversold_rebound"]["allowed_market_regimes"])
            return ("REVIEW", ["OVERSOLD_YELLOW_REVIEW_ONLY"], 0) if allowed else ("BLOCK", ["OVERSOLD_REGIME_NOT_ALLOWED"], 0)
        if strategy_id == "OVERSOLD_REBOUND" and market_regime == "PANIC_AND_REPAIR" and reversal_confirmation:
            return "REVIEW", ["OVERSOLD_RED_REVIEW_ONLY"], 0
        return "BLOCK", ["STRATEGY_BLOCKED_IN_RED_EMOTION"], 0


def _number(value: Any) -> float | None:
    try: return float(value) if value is not None else None
    except (TypeError, ValueError): return None


def _clamp(value: float) -> float:
    return max(0, min(100, value))
