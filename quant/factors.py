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
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    def calculate(self, data: QuantFactorInput) -> tuple[Decimal, list[QuantFactorScore]]:
        adjustment = data.price_adjustment or {}
        basis = str(adjustment.get("technical_price_basis") or "RAW")
        raw_bars = data.raw_kline_bars or data.kline_bars
        current_price = data.realtime_quote.current_price
        raw_current_price = raw_bars[-1].close if raw_bars else current_price
        technical_score, values = self._component_scores(
            data.kline_bars,
            current_price,
            raw_bars if basis != "RAW" else data.kline_bars,
            raw_current_price if basis != "RAW" else current_price,
        )
        raw_score = technical_score
        if basis != "RAW":
            raw_score, _ = self._component_scores(raw_bars, raw_current_price, raw_bars, raw_current_price)
        delta = technical_score - raw_score

        closes = [bar.close for bar in data.kline_bars]
        ma60 = indicators.moving_average(closes, 60)
        macd_fast, macd_slow = indicators.macd(closes)
        macd_spread = macd_fast - macd_slow if macd_fast is not None and macd_slow is not None else None
        return_5d = indicators.rolling_return(closes, 5)
        return_20d = indicators.rolling_return(closes, 20)
        drawdown = indicators.max_drawdown(closes, min(60, len(closes))) if len(closes) > 1 else None
        historical_high = max(closes[-60:]) if closes else None
        historical_low = min(closes[-60:]) if closes else None
        ma5 = values["ma5"]
        ma20 = values["ma20"]
        trend_structure = Decimal("1") if ma5 is not None and ma20 is not None and ma5 >= ma20 else Decimal("0")
        rsi_value = values["rsi"]
        atr_value = values["atr"]
        vwap_value = values["vwap"]
        ma_trend_score = values["ma_score"]
        rsi_score = values["rsi_score"]
        atr_stability_score = values["atr_score"]
        vwap_position_score = values["vwap_score"]
        basis_code = Decimal("0") if basis == "RAW" else Decimal("1")

        details = [
            _detail(data, "technical", "ma_trend_score", ma5, ma_trend_score, "MA5 relative to MA20 trend score."),
            _detail(data, "technical", "ma60", ma60, Decimal("50"), f"MA60 diagnostic on {basis} prices."),
            _detail(data, "technical", "macd_spread", macd_spread, Decimal("50"), f"MACD fast/slow EMA spread on {basis} prices."),
            _detail(data, "technical", "trend_structure", trend_structure, Decimal("50"), f"MA5 >= MA20 trend structure on {basis} prices."),
            _detail(data, "technical", "rsi_score", rsi_value, rsi_score, "RSI near short-term sweet spot receives higher score."),
            _detail(data, "technical", "atr_stability_score", atr_value, atr_stability_score, f"ATR uses a consistent {basis} OHLC/pre_close basis."),
            _detail(data, "technical", "vwap_position_score", vwap_value, vwap_position_score, "VWAP basis is RAW because amount and volume are not adjusted."),
            _detail(data, "technical", "return_5d", return_5d, Decimal("50"), f"5-day return diagnostic on {basis} prices."),
            _detail(data, "technical", "return_20d", return_20d, Decimal("50"), f"20-day return diagnostic on {basis} prices."),
            _detail(data, "technical", "historical_drawdown", drawdown, Decimal("50"), f"Historical drawdown diagnostic on {basis} prices."),
            _detail(data, "technical", "historical_high", historical_high, Decimal("50"), f"60-session high on {basis} prices."),
            _detail(data, "technical", "historical_low", historical_low, Decimal("50"), f"60-session low on {basis} prices."),
            _detail(data, "technical", "price_adjustment_basis", basis_code, technical_score, f"technical_price_basis={basis}; requested_mode={adjustment.get('mode', 'RAW')}"),
            _detail(data, "technical", "raw_technical_score", raw_score, raw_score, "RAW_BASELINE technical score."),
            _detail(data, "technical", "active_technical_score", technical_score, technical_score, f"Active technical score uses {basis}."),
            _detail(data, "technical", "technical_score_delta", delta, clamp_score(Decimal("50") + delta), "ADJUSTED_CANDIDATE minus RAW_BASELINE."),
        ]
        if basis != "RAW":
            details.append(_detail(data, "technical", "adjusted_technical_score", technical_score, technical_score, "Point-in-time adjusted technical score."))
        return technical_score, details

    @staticmethod
    def _component_scores(bars, current_price, vwap_bars, vwap_current_price):
        closes = [bar.close for bar in bars]
        ma5 = indicators.moving_average(closes, 5)
        ma20 = indicators.moving_average(closes, 20)
        rsi_value = indicators.rsi(closes, 14)
        atr_value = indicators.atr(bars, 14)
        vwap_value = indicators.simple_vwap(vwap_bars[-20:])

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
            vwap_gap = (vwap_current_price - vwap_value) / vwap_value * Decimal("100")
            vwap_position_score = _score(vwap_gap, -5, 5)

        technical_score = average_score(
            [ma_trend_score, rsi_score, atr_stability_score, vwap_position_score]
        )
        return technical_score, {
            "ma5": ma5,
            "ma20": ma20,
            "rsi": rsi_value,
            "atr": atr_value,
            "vwap": vwap_value,
            "ma_score": ma_trend_score,
            "rsi_score": rsi_score,
            "atr_score": atr_stability_score,
            "vwap_score": vwap_position_score,
        }


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
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    def calculate(self, data: QuantFactorInput) -> tuple[Decimal, list[QuantFactorScore]]:
        closes = [bar.close for bar in data.kline_bars]
        volatility_value = indicators.volatility(closes, 20)
        drawdown_value = indicators.max_drawdown(closes, 20)
        liquidity_value = data.realtime_quote.amount
        volatility_risk_score = _score(volatility_value, 8, 1, higher_is_better=True)
        drawdown_risk_score = _score(drawdown_value, 20, 1, higher_is_better=True)
        liquidity_risk_score = _score(liquidity_value, 20_000_000, 200_000_000)
        price_limit_config = self.config.get("price_limit", {})
        price_limit_enabled = bool(price_limit_config.get("enabled", False))
        if not price_limit_enabled:
            risk_score = average_score(
                [volatility_risk_score, drawdown_risk_score, liquidity_risk_score]
            )
        else:
            weights = {
                key: Decimal(str(value))
                for key, value in self.config.get("internal_weights", {}).items()
            }
            required = {"volatility", "drawdown", "liquidity", "financial", "price_limit"}
            if set(weights) != required or abs(sum(weights.values(), Decimal("0")) - Decimal("1")) > Decimal("0.0001"):
                raise ValueError("risk_factor.internal_weights must contain five weights summing to 1.0")
            debt_ratio = data.finance_snapshot.debt_ratio if data.finance_snapshot else None
            financial_score = _score(debt_ratio, 80, 20, higher_is_better=True) if debt_ratio and debt_ratio > 0 else Decimal("50")
            price_limit_score = clamp_score(data.price_limit_risk.get("price_limit_risk_score", 50))
            sub_scores = {
                "volatility": volatility_risk_score,
                "drawdown": drawdown_risk_score,
                "liquidity": liquidity_risk_score,
                "financial": financial_score,
                "price_limit": price_limit_score,
            }
            risk_score = clamp_score(sum((sub_scores[key] * weights[key] for key in required), Decimal("0")))
        details = [
            _detail(data, "risk", "volatility_risk_score", volatility_value, volatility_risk_score, "波动越低，风险健康分越高。"),
            _detail(data, "risk", "drawdown_risk_score", drawdown_value, drawdown_risk_score, "回撤越小，风险健康分越高。"),
            _detail(data, "risk", "liquidity_risk_score", liquidity_value, liquidity_risk_score, "成交额越高，流动性风险越低。"),
        ]
        if price_limit_enabled:
            weights = {key: Decimal(str(value)) for key, value in self.config["internal_weights"].items()}
            price_limit_score = clamp_score(data.price_limit_risk.get("price_limit_risk_score", 50))
            price_limit_weight = weights["price_limit"]
            details.extend(
                [
                    _detail(data, "risk", "financial_risk_score", data.finance_snapshot.debt_ratio if data.finance_snapshot else None, financial_score, "Financial risk is neutral when point-in-time debt data is unavailable.", weights["financial"]),
                    _detail(data, "risk", "price_limit_risk_score", price_limit_score, price_limit_score, f"Price-limit risk health score; higher is safer. status={data.price_limit_risk.get('limit_status', 'LIMIT_DATA_MISSING')}", price_limit_weight),
                    _detail(data, "risk", "price_limit_internal_weight", price_limit_weight, price_limit_score, "Internal weight inside the risk factor group.", price_limit_weight),
                    _detail(data, "risk", "price_limit_weighted_contribution", price_limit_score * price_limit_weight, price_limit_score, "Price-limit contribution to risk health score.", price_limit_weight),
                ]
            )
        return risk_score, details
