from __future__ import annotations

from decimal import Decimal

from quant import indicators
from quant.normalizer import average_score, clamp_score, min_max_normalize
from quant.schemas import QuantFactorInput, QuantFactorScore


def _score(value, min_value, max_value, higher_is_better: bool = True) -> Decimal:
    return min_max_normalize(value, min_value, max_value, higher_is_better=higher_is_better)


def _detail(
    data: QuantFactorInput,
    group: str,
    name: str,
    raw_value,
    score,
    explain_text: str,
    weight: Decimal | None = None,
) -> QuantFactorScore:
    return QuantFactorScore(
        stock_code=data.stock_code,
        factor_group=group,
        factor_name=name,
        raw_value=Decimal(str(raw_value)) if raw_value is not None else None,
        normalized_value=score,
        score=score,
        weight=weight,
        explain_text=explain_text,
    )


class TechnicalFactorCalculator:
    def calculate(self, data: QuantFactorInput) -> tuple[Decimal, list[QuantFactorScore]]:
        closes = [bar.close for bar in data.kline_bars]
        current_price = data.realtime_quote.current_price
        ma5 = indicators.moving_average(closes, 5)
        ma20 = indicators.moving_average(closes, 20)
        rsi_value = indicators.rsi(closes, 14)
        atr_value = indicators.atr(data.kline_bars, 14)
        vwap_value = indicators.simple_vwap(data.kline_bars[-20:])

        ma_trend_score = Decimal("50.0000")
        if ma5 is not None and ma20 is not None:
            ma_spread = (ma5 - ma20) / ma20 * Decimal("100") if ma20 else Decimal("0")
            ma_trend_score = _score(ma_spread, -5, 5)

        rsi_score = Decimal("50.0000")
        if rsi_value is not None:
            rsi_score = clamp_score(100 - abs(Decimal("55") - rsi_value) * Decimal("2"))

        atr_stability_score = Decimal("50.0000")
        if atr_value is not None and current_price:
            atr_percent = atr_value / current_price * Decimal("100")
            atr_stability_score = _score(atr_percent, 8, 1, higher_is_better=True)

        vwap_position_score = Decimal("50.0000")
        if vwap_value is not None and vwap_value:
            vwap_gap = (current_price - vwap_value) / vwap_value * Decimal("100")
            vwap_position_score = _score(vwap_gap, -5, 5)

        technical_score = average_score(
            [ma_trend_score, rsi_score, atr_stability_score, vwap_position_score]
        )
        details = [
            _detail(data, "technical", "ma_trend_score", ma5, ma_trend_score, "MA5 relative to MA20 trend score."),
            _detail(data, "technical", "rsi_score", rsi_value, rsi_score, "RSI near short-term sweet spot receives higher score."),
            _detail(data, "technical", "atr_stability_score", atr_value, atr_stability_score, "Lower ATR percent means healthier short-term stability."),
            _detail(data, "technical", "vwap_position_score", vwap_value, vwap_position_score, "Price position versus VWAP."),
        ]
        return technical_score, details


class CapitalFactorCalculator:
    def calculate(self, data: QuantFactorInput) -> tuple[Decimal, list[QuantFactorScore]]:
        amount_score = _score(data.realtime_quote.amount, 50_000_000, 300_000_000)
        volume_ratio_score = Decimal("50.0000")
        turnover_score = Decimal("50.0000")

        if data.capital_flow is not None:
            volume_ratio_score = _score(data.capital_flow.volume_ratio, 0.8, 2.5)
            turnover_score = _score(data.capital_flow.turnover_rate, 0.5, 8)
            inflow_score = _score(data.capital_flow.main_net_inflow, -5_000_000, 20_000_000)
        else:
            inflow_score = Decimal("50.0000")

        capital_score = average_score(
            [amount_score, volume_ratio_score, turnover_score, inflow_score]
        )
        details = [
            _detail(data, "capital", "amount_score", data.realtime_quote.amount, amount_score, "成交额越高，短线可交易性越好。"),
            _detail(data, "capital", "volume_ratio_score", data.capital_flow.volume_ratio if data.capital_flow else None, volume_ratio_score, "量比反映资金活跃度。"),
            _detail(data, "capital", "turnover_score", data.capital_flow.turnover_rate if data.capital_flow else None, turnover_score, "换手率处于合理活跃区间得分更高。"),
            _detail(data, "capital", "main_inflow_score", data.capital_flow.main_net_inflow if data.capital_flow else None, inflow_score, "主力净流入越强得分越高。"),
        ]
        return capital_score, details


class EmotionFactorCalculator:
    def calculate(self, data: QuantFactorInput) -> tuple[Decimal, list[QuantFactorScore]]:
        if data.market_emotion is None:
            score = Decimal("50.0000")
            return score, [_detail(data, "emotion", "emotion_score", None, score, "No market emotion snapshot.")]

        limit_up_environment_score = _score(data.market_emotion.limit_up_count, 10, 80)
        limit_down_penalty_score = _score(data.market_emotion.limit_down_count, 30, 0, higher_is_better=True)
        market_heat_score = clamp_score(data.market_emotion.emotion_score)
        emotion_score = average_score(
            [limit_up_environment_score, limit_down_penalty_score, market_heat_score]
        )
        details = [
            _detail(data, "emotion", "limit_up_environment_score", data.market_emotion.limit_up_count, limit_up_environment_score, "涨停数量反映短线情绪。"),
            _detail(data, "emotion", "limit_down_control_score", data.market_emotion.limit_down_count, limit_down_penalty_score, "跌停数量越少，风险环境越健康。"),
            _detail(data, "emotion", "market_heat_score", data.market_emotion.emotion_score, market_heat_score, "市场情绪综合分。"),
        ]
        return emotion_score, details


class MomentumFactorCalculator:
    def __init__(self, r5_weight: Decimal = Decimal("0.60"), r20_weight: Decimal = Decimal("0.40")) -> None:
        self.r5_weight = r5_weight
        self.r20_weight = r20_weight

    def calculate(self, data: QuantFactorInput) -> tuple[Decimal, list[QuantFactorScore]]:
        closes = [bar.close for bar in data.kline_bars]
        return_5d = indicators.rolling_return(closes, 5)
        return_20d = indicators.rolling_return(closes, 20)
        return_5d_score = _score(return_5d, -8, 12)
        return_20d_score = _score(return_20d, -15, 25)
        momentum_score = clamp_score(
            return_5d_score * self.r5_weight + return_20d_score * self.r20_weight
        )
        details = [
            _detail(data, "momentum", "return_5d_score", return_5d, return_5d_score, "5日收益动量。", self.r5_weight),
            _detail(data, "momentum", "return_20d_score", return_20d, return_20d_score, "20日收益动量。", self.r20_weight),
        ]
        return momentum_score, details


class RiskFactorCalculator:
    def calculate(self, data: QuantFactorInput) -> tuple[Decimal, list[QuantFactorScore]]:
        closes = [bar.close for bar in data.kline_bars]
        volatility_value = indicators.volatility(closes, 20)
        drawdown_value = indicators.max_drawdown(closes, 20)
        liquidity_value = data.realtime_quote.amount
        volatility_risk_score = _score(volatility_value, 8, 1, higher_is_better=True)
        drawdown_risk_score = _score(drawdown_value, 20, 1, higher_is_better=True)
        liquidity_risk_score = _score(liquidity_value, 20_000_000, 200_000_000)
        risk_score = average_score(
            [volatility_risk_score, drawdown_risk_score, liquidity_risk_score]
        )
        details = [
            _detail(data, "risk", "volatility_risk_score", volatility_value, volatility_risk_score, "波动越低，风险健康分越高。"),
            _detail(data, "risk", "drawdown_risk_score", drawdown_value, drawdown_risk_score, "回撤越小，风险健康分越高。"),
            _detail(data, "risk", "liquidity_risk_score", liquidity_value, liquidity_risk_score, "成交额越高，流动性风险越低。"),
        ]
        return risk_score, details
