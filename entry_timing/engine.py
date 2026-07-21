from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean
from typing import Any


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _ratio(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return current / previous - 1


@dataclass(frozen=True)
class EntryTimingInput:
    stock_code: str
    quant_score: float
    quant_rank: int | None
    risk_score: float | None
    bars: list[dict[str, Any]]
    daily_basic: dict[str, Any] = field(default_factory=dict)
    moneyflow: dict[str, Any] = field(default_factory=dict)
    limit_data: dict[str, Any] = field(default_factory=dict)
    sector_change: float | None = None
    concept_change: float | None = None
    market_regime: str = "UNKNOWN"
    realtime: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EntryTimingAssessment:
    position_score: float
    pullback_score: float
    volume_price_score: float
    sector_score: float
    market_score: float
    liquidity_score: float
    entry_timing_score: float
    data_quality_score: float
    timing_status: str
    risk_flags: list[str]
    diagnostics: dict[str, Any]


class EntryTimingEngine:
    """Deterministic 1-10 trading-day entry timing scorer."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def evaluate(self, item: EntryTimingInput) -> EntryTimingAssessment:
        bars = sorted(item.bars, key=lambda row: str(row.get("trade_date") or ""))
        closes = [float(row["adj_close"]) for row in bars if row.get("adj_close") is not None]
        current = bars[-1] if bars else {}
        close = float(current["adj_close"]) if current.get("adj_close") is not None else None
        ret1 = _ratio(close, float(bars[-2]["adj_close"])) if close is not None and len(bars) >= 2 else None
        ret5 = _ratio(close, float(bars[-6]["adj_close"])) if close is not None and len(bars) >= 6 else None
        ret10 = _ratio(close, float(bars[-11]["adj_close"])) if close is not None and len(bars) >= 11 else None
        ret20 = _ratio(close, float(bars[-21]["adj_close"])) if close is not None and len(bars) >= 21 else None
        ma5 = fmean(closes[-5:]) if len(closes) >= 5 else None
        ma10 = fmean(closes[-10:]) if len(closes) >= 10 else None
        bias5 = _ratio(close, ma5)
        bias10 = _ratio(close, ma10)
        high20 = max(closes[-20:]) if closes else None
        high60 = max(closes[-60:]) if closes else None
        distance20 = (high20 - close) / high20 if close is not None and high20 else None
        distance60 = (high60 - close) / high60 if close is not None and high60 else None
        amounts = [float(row.get("amount") or 0) for row in bars]
        previous_amounts = amounts[-6:-1] if len(amounts) >= 6 else amounts[:-1]
        amount_ratio = amounts[-1] / fmean(previous_amounts) if amounts and previous_amounts and fmean(previous_amounts) else None
        turnover = self._number(item.daily_basic.get("turnover_rate"))
        net_flow = self._number(item.moneyflow.get("net_mf_amount"))

        metrics = [ret5, ret10, high20, ma5, ma10, amount_ratio, turnover, net_flow, item.sector_change]
        available = sum(value is not None for value in metrics) + int(item.market_regime != "UNKNOWN")
        data_quality = available / 10

        weights = self.config.get("weights") or {}
        position = self._scaled(self._position(ret5, ret10, ret20, distance20, distance60, bias5, bias10), 25, weights.get("position", 25))
        pullback = self._scaled(self._pullback(bars, close, high20, ma5, ma10, amount_ratio), 20, weights.get("pullback", 20))
        volume_price = self._scaled(self._volume_price(ret1, amount_ratio, turnover, net_flow), 20, weights.get("volume_price", 20))
        sector = self._scaled(self._sector(ret1, item.sector_change, item.concept_change), 15, weights.get("sector_resonance", 15))
        market = self._scaled(self._market(item.market_regime), 10, weights.get("market_fit", 10))
        liquidity = self._scaled(self._liquidity(current, turnover, item.limit_data), 10, weights.get("liquidity", 10))
        total = position + pullback + volume_price + sector + market + liquidity
        flags = self._risk_flags(ret1, ret5, ret10, distance20, amount_ratio, volume_price)
        minimum_quality = float(self.config.get("minimum_data_quality", 0.55))
        timing_status = "DATA_INSUFFICIENT" if data_quality < minimum_quality else (
            "APPROVED" if total >= 70 else "WATCH" if total >= 60 else "BLOCKED"
        )
        return EntryTimingAssessment(
            position_score=round(position, 4), pullback_score=round(pullback, 4),
            volume_price_score=round(volume_price, 4), sector_score=round(sector, 4),
            market_score=round(market, 4), liquidity_score=round(liquidity, 4),
            entry_timing_score=round(total, 4), data_quality_score=round(data_quality * 100, 4),
            timing_status=timing_status, risk_flags=flags,
            diagnostics={
                "return_1d": ret1, "return_5d": ret5, "return_10d": ret10, "return_20d": ret20,
                "distance_20d_high": distance20, "distance_60d_high": distance60,
                "bias5": bias5, "bias10": bias10, "ma5": ma5, "ma10": ma10,
                "amount_ratio": amount_ratio, "turnover_rate": turnover, "net_mf_amount": net_flow,
                "sector_change": item.sector_change, "concept_change": item.concept_change,
                "market_regime": item.market_regime, "bar_count": len(bars),
            },
        )

    @staticmethod
    def _position(ret5, ret10, ret20, distance20, distance60, bias5, bias10) -> float:
        score = 22.0
        if ret5 is not None and distance20 is not None and ret5 > 0.25 and distance20 < 0.03:
            score -= 15
        elif ret5 is not None and ret5 > 0.15:
            score -= 7
        if ret10 is not None and ret10 > 0.30:
            score -= 5
        if ret20 is not None and ret20 > 0.45:
            score -= 3
        if bias5 is not None:
            score -= 5 if bias5 > 0.08 else 2 if bias5 > 0.04 else 0
        if bias10 is not None and bias10 > 0.12:
            score -= 3
        if distance20 is not None and 0.03 <= distance20 <= 0.12:
            score += 3
        if distance60 is not None and distance60 > 0.35:
            score -= 2
        return _clamp(score, 0, 25)

    @staticmethod
    def _pullback(bars, close, high20, ma5, ma10, amount_ratio) -> float:
        if close is None or high20 is None:
            return 8.0
        drawdown = (high20 - close) / high20 if high20 else 0
        if 0.02 <= drawdown <= 0.10 and amount_ratio is not None and amount_ratio <= 0.90 and ma5 and close >= ma5:
            return 19.0
        if 0.02 <= drawdown <= 0.12 and amount_ratio is not None and amount_ratio <= 1.0:
            return 16.0
        if drawdown > 0.15 or (ma10 and close < ma10 and amount_ratio is not None and amount_ratio > 1.20):
            return 4.0
        if drawdown < 0.02 and amount_ratio is not None and amount_ratio > 1.4:
            return 7.0
        return 11.0

    @staticmethod
    def _volume_price(ret1, amount_ratio, turnover, net_flow) -> float:
        score = 10.0
        if ret1 is not None and amount_ratio is not None:
            if ret1 > 0.01 and amount_ratio >= 1.20:
                score += 6
            elif ret1 < -0.01 and amount_ratio >= 1.20:
                score -= 7
            elif abs(ret1) < 0.01 and amount_ratio > 1.50:
                score -= 5
            elif ret1 <= 0 and amount_ratio <= 0.85:
                score += 3
        if net_flow is not None:
            score += 2 if net_flow > 0 else -2
        if turnover is not None:
            score += 2 if 1 <= turnover <= 12 else -4 if turnover > 25 else 0
        return _clamp(score, 0, 20)

    @staticmethod
    def _sector(stock_return, sector_change, concept_change) -> float:
        if sector_change is None:
            return 7.5
        score = 7.0
        if sector_change > 0:
            score += 3
        if sector_change > 0.01:
            score += 2
        if stock_return is not None and stock_return > 0 and sector_change > 0:
            score += 3
        if stock_return is not None and stock_return > 0.02 and sector_change < 0:
            score -= 4
        if concept_change is not None:
            score += 2 if concept_change > 0 else -1
        return _clamp(score, 0, 15)

    def _market(self, regime: str) -> float:
        admission = self.config.get("admission") or {}
        if regime in set(admission.get("red_market_regimes") or []):
            return 3.0
        if regime in set(admission.get("yellow_market_regimes") or []):
            return 6.5
        return 10.0 if regime != "UNKNOWN" else 5.0

    @staticmethod
    def _liquidity(current, turnover, limit_data) -> float:
        amount_yuan = float(current.get("amount") or 0) * 1000
        score = 10.0 if amount_yuan >= 500_000_000 else 8.0 if amount_yuan >= 100_000_000 else 6.0 if amount_yuan >= 30_000_000 else 3.0
        if turnover is not None and turnover > 25:
            score -= 3
        close = EntryTimingEngine._number(current.get("close"))
        up_limit = EntryTimingEngine._number(limit_data.get("up_limit"))
        down_limit = EntryTimingEngine._number(limit_data.get("down_limit"))
        if close is not None and up_limit is not None and close >= up_limit - 0.001:
            score -= 3
        if close is not None and down_limit is not None and close <= down_limit + 0.001:
            score = 0
        return _clamp(score, 0, 10)

    def _risk_flags(self, ret1, ret5, ret10, distance20, amount_ratio, volume_price) -> list[str]:
        cfg = self.config.get("high_position_risk") or {}
        flags: list[str] = []
        if ret5 is not None and distance20 is not None and ret5 > float(cfg.get("five_day_return", 0.30)) and distance20 < float(cfg.get("twenty_day_high_distance", 0.02)):
            flags.append("HIGH_CHASE_RISK")
        if ret10 is not None and ret10 > float(cfg.get("ten_day_return", 0.50)) and volume_price < 10:
            flags.append("MOMENTUM_EXHAUSTION")
        if amount_ratio is not None and ret1 is not None and amount_ratio > float(cfg.get("distribution_amount_ratio", 1.50)) and ret1 < float(cfg.get("distribution_max_return", 0.01)):
            flags.append("DISTRIBUTION_RISK")
        return flags

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _scaled(value: float, base_maximum: float, configured_maximum: Any) -> float:
        maximum = float(configured_maximum)
        if maximum < 0:
            raise ValueError("ENTRY_TIMING_WEIGHT_MUST_BE_NON_NEGATIVE")
        return _clamp(value / base_maximum * maximum, 0, maximum)
