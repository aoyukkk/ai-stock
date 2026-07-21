from __future__ import annotations

import json
import os
import re
from typing import Any

from llm_gateway.schemas import LLMMessage, LLMRequest
from llm_gateway.service import LLMGatewayService
from market_review.schemas import (
    DriverWire,
    MarketDailyReviewWireV1,
    MarketDailySnapshotData,
    MarketEvidence,
    MarketOutlookResult,
    MarketRegimeResult,
    OutlookScenarioWire,
    TomorrowOutlookWire,
)


DISCLAIMER = "本报告基于已取得的市场数据、公开信息与规则模型生成，仅用于市场复盘和模型验证，不构成投资建议。次日走势为条件情景分析，不是确定性预测。"
BANNED_PHRASES = ("明日必涨", "明日必跌", "确定突破", "确定见顶", "保证上涨", "完全因为", "唯一原因", "已证明导致")


class DataOnlyReviewBuilder:
    def build(
        self,
        snapshot: MarketDailySnapshotData,
        regime: MarketRegimeResult,
        outlook: MarketOutlookResult,
        *,
        search_status: str,
        evidence: list[MarketEvidence],
    ) -> MarketDailyReviewWireV1:
        breadth = snapshot.breadth
        turnover = snapshot.turnover
        confirmed = [self._evidence_driver(item) for item in evidence if item.status in {"VERIFIED_OFFICIAL", "MULTI_SOURCE_SUPPORTED"}]
        probable = [self._evidence_driver(item) for item in evidence if item.status == "SINGLE_SOURCE_PROBABLE"]
        structural = [
            DriverWire(
                title="市场宽度与等权表现",
                direction=_direction(float(breadth.get("equal_weight_return") or 0)),
                impact_strength=min(100, round(abs(float(breadth.get("equal_weight_return") or 0)) * 1500 + 35)),
                confidence=min(1.0, snapshot.data_quality_score / 100),
                metric_ids=["breadth.advancing_ratio", "breadth.equal_weight_return", "breadth.median_return"],
                explanation=(
                    f"上涨{int(breadth.get('advancing_count') or 0)}家、下跌{int(breadth.get('declining_count') or 0)}家，"
                    f"等权涨跌幅{float(breadth.get('equal_weight_return') or 0):.2%}。"
                ),
            ),
            DriverWire(
                title="成交与流动性结构",
                direction=_direction(float(turnover.get("change_ratio") or 0)),
                impact_strength=min(100, round(abs(float(turnover.get("change_ratio") or 0)) * 100 + 30)),
                confidence=0.9 if turnover.get("status") == "AVAILABLE" else 0.45,
                metric_ids=["turnover.total_amount", "turnover.change_ratio", "turnover.turnover_state"],
                explanation=f"全市场成交额{_yi(float(turnover.get('total_amount') or 0))}亿元，成交状态为{turnover.get('turnover_state')}。",
            ),
        ]
        scenarios = {item.scenario_type: item for item in outlook.scenarios}
        tomorrow = TomorrowOutlookWire(
            base_case=self._scenario(scenarios["BASE"].title, outlook.base_case_probability, snapshot, "BASE"),
            bull_case=self._scenario(scenarios["BULL"].title, outlook.bull_case_probability, snapshot, "BULL"),
            bear_case=self._scenario(scenarios["BEAR"].title, outlook.bear_case_probability, snapshot, "BEAR"),
        )
        evidence_warning = search_status in {"UNAVAILABLE", "DATA_ONLY"}
        return MarketDailyReviewWireV1(
            schema_version="market_daily_review_wire_v1",
            trade_date=snapshot.trade_date,
            market_direction=snapshot.market_direction,
            market_regime=regime.market_regime,
            headline=f"{snapshot.trade_date.isoformat()} A股市场：{_direction_label(snapshot.market_direction)}，状态为{regime.market_regime}",
            market_summary=(
                f"当日可计算股票{int(breadth.get('valid_count') or 0)}只，等权涨跌幅"
                f"{float(breadth.get('equal_weight_return') or 0):.2%}。"
                + ("联网证据不足，本次原因分析以本地市场结构为主。" if evidence_warning else "公开证据已按来源和时间完成筛选。")
            ),
            breadth_summary=f"上涨占比{float(breadth.get('advancing_ratio') or 0):.2%}，中位数涨跌幅{float(breadth.get('median_return') or 0):.2%}。",
            turnover_summary=f"成交额{_yi(float(turnover.get('total_amount') or 0))}亿元，较上一交易日{_percent_text(turnover.get('change_ratio'))}。",
            style_summary=f"相对较强风格为{snapshot.style.get('dominant_style')}，相对较弱风格为{snapshot.style.get('weak_style')}。",
            confirmed_drivers=confirmed,
            probable_explanations=probable,
            structural_observations=structural,
            tomorrow_outlook=tomorrow,
            key_watch_items=["主要指数与全市场宽度是否同向", "成交额相对5日均值的变化", "领涨行业和概念的扩散持续性"],
            main_risks=["指数本地数据不足时，市场方向主要依赖全市场等权结构", "次日情景概率是规则计算结果，不是确定性预测"],
            data_conflict=bool(regime.conflicting_metrics),
            search_status=search_status,
            confidence=round(min(snapshot.data_quality_score / 100, regime.regime_confidence), 4),
        )

    @staticmethod
    def _evidence_driver(item: MarketEvidence) -> DriverWire:
        return DriverWire(
            title=item.title,
            direction=item.direction,
            impact_strength=round(item.final_evidence_score * 100),
            confidence=item.final_evidence_score,
            affected_scope=item.affected_scope,
            affected_sectors=item.affected_sectors,
            evidence_ids=[item.evidence_id],
            explanation=f"公开信息显示：{item.summary}",
        )

    @staticmethod
    def _scenario(title: str, probability: int, snapshot: MarketDailySnapshotData, scenario_type: str) -> OutlookScenarioWire:
        if scenario_type == "BASE":
            description = "若量能与市场宽度维持当前组合，市场更可能延续当前节奏并伴随结构分化。"
            triggers = ["上涨家数占比维持", "成交额未明显失速"]
            invalidation = ["市场宽度快速反向", "成交额和主要板块同步走弱"]
        elif scenario_type == "BULL":
            description = "若成交放大且强势板块向更多行业扩散，市场可能出现更广泛的修复。"
            triggers = ["成交额高于5日均值", "上涨家数占比明显提升"]
            invalidation = ["放量但下跌家数增加", "领涨板块快速退潮"]
        else:
            description = "若量能收缩且亏钱效应扩散，市场可能出现下探或冲高回落。"
            triggers = ["下跌家数占比提升", "跌停和炸板数量增加"]
            invalidation = ["市场宽度转强", "缩量后出现普涨修复"]
        return OutlookScenarioWire(
            probability=probability,
            title=title,
            description=description,
            supporting_reasons=[f"当前市场方向为{snapshot.market_direction}", f"数据质量得分{snapshot.data_quality_score:.0f}"],
            triggers=triggers,
            invalidation_conditions=invalidation,
            watch_items=["市场宽度", "成交额", "涨跌停结构", "领涨板块扩散度"],
        )


class GatewayMarketReviewPro:
    def __init__(self, session, config: dict[str, Any]) -> None:
        self.gateway = LLMGatewayService(db_session=session)
        self.config = config

    def generate(self, input_payload: dict[str, Any], *, allow_real_pro: bool) -> tuple[dict[str, Any], int | None]:
        if not allow_real_pro or os.getenv("MARKET_REVIEW_REAL_PRO_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
            raise RuntimeError("REAL_MARKET_REVIEW_PRO_NOT_APPROVED")
        request = LLMRequest(
            agent_name="market_review_pro",
            task="daily_review_final",
            task_type="daily_review_final",
            model_alias=str(self.config.get("model_alias", "controller-high-capability")),
            messages=[
                LLMMessage(role="system", content="根据输入事实生成A股每日大盘复盘。只返回JSON，不得修改概率、事实、证据ID或metric ID，不得生成URL或买卖指令。"),
                LLMMessage(role="user", content=json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))),
            ],
            temperature=float(self.config.get("temperature", 0.1)),
            max_tokens=int(self.config.get("max_tokens", 8000)),
            thinking_mode=str(self.config.get("thinking", "disabled")),
            json_mode=True,
            response_schema=MarketDailyReviewWireV1.model_json_schema(),
            allow_fallback=False,
            prompt_version="market_daily_review_prompt_v1",
            metadata={"explicit_real_llm_test": True, "market_review": True},
        )
        response = self.gateway.chat(request)
        if response.status != "ok" or response.structured_output is None:
            raise RuntimeError(f"MARKET_REVIEW_PRO_FAILED:{response.status}")
        return dict(response.structured_output), response.usage_id


def validate_wire_facts(
    payload: dict[str, Any],
    snapshot: MarketDailySnapshotData,
    regime: MarketRegimeResult,
    outlook: MarketOutlookResult,
    evidence: list[MarketEvidence],
) -> MarketDailyReviewWireV1:
    text = json.dumps(payload, ensure_ascii=False)
    if any(phrase in text for phrase in BANNED_PHRASES):
        raise ValueError("DETERMINISTIC_PREDICTION_LANGUAGE_REJECTED")
    if re.search(r"https?://|买入|卖出|建仓|加仓|清仓|\d{4,5}(?:\.\d+)?点", text):
        raise ValueError("FORBIDDEN_MARKET_REVIEW_OUTPUT")
    wire = MarketDailyReviewWireV1.model_validate(payload)
    if wire.trade_date != snapshot.trade_date or wire.market_direction != snapshot.market_direction or wire.market_regime != regime.market_regime:
        raise ValueError("MARKET_REVIEW_FACT_MISMATCH")
    expected = (outlook.base_case_probability, outlook.bull_case_probability, outlook.bear_case_probability)
    actual = (wire.tomorrow_outlook.base_case.probability, wire.tomorrow_outlook.bull_case.probability, wire.tomorrow_outlook.bear_case.probability)
    if actual != expected or sum(actual) != 100:
        raise ValueError("MARKET_REVIEW_PROBABILITY_MISMATCH")
    evidence_ids = {item.evidence_id for item in evidence}
    metric_ids = set(snapshot.metric_ids)
    for driver in wire.confirmed_drivers + wire.probable_explanations + wire.structural_observations:
        if not set(driver.evidence_ids).issubset(evidence_ids) or not set(driver.metric_ids).issubset(metric_ids):
            raise ValueError("MARKET_REVIEW_REFERENCE_MISMATCH")
    if wire.search_status in {"UNAVAILABLE", "DATA_ONLY"} and wire.confirmed_drivers:
        raise ValueError("CONFIRMED_DRIVER_REQUIRES_EVIDENCE")
    return wire


def _direction(value: float) -> str:
    return "POSITIVE" if value > 0 else "NEGATIVE" if value < 0 else "NEUTRAL"


def _direction_label(value: str) -> str:
    return {"UP": "整体上涨", "DOWN": "整体下跌", "FLAT": "窄幅震荡", "MIXED": "结构分化"}.get(value, value)


def _yi(value: float) -> str:
    return f"{value / 100_000_000:.2f}"


def _percent_text(value: Any) -> str:
    if value is None:
        return "暂无可比数据"
    number = float(value)
    return f"{'增加' if number >= 0 else '减少'}{abs(number):.2%}"
