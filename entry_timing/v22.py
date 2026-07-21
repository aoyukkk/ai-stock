from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from backend.core.config import AppConfig, get_app_config


class EntryTimingV22ConfigService:
    def __init__(self, app_config: AppConfig | None = None) -> None:
        self.app_config = app_config or get_app_config()

    def get(self) -> dict[str, Any]:
        value = deepcopy(self.app_config.config_files.get("entry_timing_v2_2", {}).get("entry_timing_v2_2", {}))
        if not value:
            raise ValueError("ENTRY_TIMING_V2_2_CONFIG_MISSING")
        safety = value.get("safety") or {}
        forbidden = ("actionable", "scheduler_enabled", "create_orders", "create_virtual_orders", "real_trading_enabled", "external_historical_calls_enabled", "llm_calls_enabled", "parameter_search_enabled")
        if any(bool(safety.get(key)) for key in forbidden):
            raise ValueError("ENTRY_TIMING_V2_2_SAFETY_CONFIG_INVALID")
        return value


@dataclass(frozen=True)
class RegimeV2Result:
    previous_state: str | None
    current_state: str
    raw_state: str
    state_reasons: list[str]
    cooldown_remaining: int
    confirmation_count: int
    input_coverage: float


class MarketRegimeV2Engine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.cfg = config["regime"]

    def evaluate(self, snapshot: dict[str, Any], history: list[RegimeV2Result]) -> RegimeV2Result:
        breadth = snapshot.get("breadth") or {}; limits = snapshot.get("limit_structure") or {}
        emotion = _num(snapshot.get("market_emotion_score"))
        eq = _num(breadth.get("equal_weight_return")); up = _num(breadth.get("advancing_ratio")); down = _num(breadth.get("declining_ratio"))
        valid = _num(breadth.get("valid_count")); below5 = _num(breadth.get("below_5_count")); below5_ratio = below5 / valid if below5 is not None and valid else None
        limit_down = _num(limits.get("limit_down_count")); industries = snapshot.get("industries") or []
        industry_up = sum(_num(row.get("change_percent")) > 0 for row in industries if _num(row.get("change_percent")) is not None)
        industry_valid = sum(_num(row.get("change_percent")) is not None for row in industries)
        industry_ratio = industry_up / industry_valid if industry_valid else None
        fields = [eq, up, down, emotion, below5_ratio, limit_down, industry_ratio]
        coverage = sum(v is not None for v in fields) / len(fields)
        reasons=[]
        crash = ((eq is not None and eq <= float(self.cfg["crash_equal_weight_return"])) or (down is not None and down >= float(self.cfg["crash_declining_ratio"])) or (below5_ratio is not None and below5_ratio >= float(self.cfg["crash_below_5_ratio"])) or (limit_down is not None and limit_down >= float(self.cfg["crash_limit_down_count"])))
        risk_off = (eq is not None and eq <= float(self.cfg["risk_off_equal_weight_return"])) or (down is not None and down >= float(self.cfg["risk_off_declining_ratio"])) or (emotion is not None and emotion < 40)
        risk_on = all((eq is not None and eq >= float(self.cfg["risk_on_equal_weight_return"]), up is not None and up >= float(self.cfg["risk_on_advancing_ratio"]), emotion is not None and emotion >= float(self.cfg["risk_on_emotion_score"]), industry_ratio is not None and industry_ratio >= float(self.cfg["risk_on_industry_advancing_ratio"])))
        raw = "CRASH" if crash else "RISK_OFF" if risk_off else "RISK_ON" if risk_on else "ROTATION"
        previous = history[-1].current_state if history else None
        prior_cooldown = history[-1].cooldown_remaining if history else 0
        if crash:
            current="CRASH"; cooldown=int(self.cfg["crash_cooldown_trading_days"]); reasons.append("CRASH_HARD_RISK_TRIGGERED")
        elif previous == "CRASH":
            current="REPAIR"; cooldown=max(prior_cooldown-1, 0); reasons.append("FIRST_SESSION_AFTER_CRASH")
        elif prior_cooldown > 0:
            current="RISK_OFF" if risk_off else "REPAIR"; cooldown=prior_cooldown-1; reasons.append("CRASH_COOLDOWN_ACTIVE")
        elif risk_off:
            current="RISK_OFF"; cooldown=0; reasons.append("BREADTH_OR_EMOTION_RISK_OFF")
        elif risk_on:
            recent=[row.raw_state for row in history[-2:]]+[raw]
            confirmations=sum(item=="RISK_ON" for item in recent)
            required=int(self.cfg["risk_on_confirmation_days"])
            current="RISK_ON" if previous!="RISK_OFF" and confirmations>=required else "REPAIR"
            reasons.append("RISK_ON_CONFIRMED" if current=="RISK_ON" else "RISK_ON_CONFIRMATION_PENDING")
            cooldown=0
        else:
            current="ROTATION"; cooldown=0; reasons.append("MIXED_OR_ROTATING_MARKET")
        recent=[row.raw_state for row in history[-2:]]+[raw]
        return RegimeV2Result(previous,current,raw,reasons,cooldown,sum(item=="RISK_ON" for item in recent),round(coverage,4))


@dataclass(frozen=True)
class DeploymentDecision:
    status: str
    reason: str
    position_multiplier: float


class RegimeDeploymentGate:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config=config

    def apply(self, rows: list[Any], regime: str, industry_strength: dict[str,float]) -> list[tuple[Any,DeploymentDecision]]:
        policy=self.config["deployment"][regime]; maximum=int(policy["maximum_candidates"]); multiplier=float(policy["position_multiplier"])
        ranked=sorted(rows,key=lambda row:(-float(getattr(row,"admission_ranking_score_v2",0) or 0),getattr(row,"quant_rank",999999) or 999999))
        output=[]; eligible=[]
        for row in ranked:
            industry=getattr(row,"industry",None); strength=industry_strength.get(industry,0)
            strategy=getattr(row,"strategy_id","")
            if regime in {"RISK_OFF","CRASH"}: decision=DeploymentDecision("REMOVED_BY_REGIME","BLOCK_NEW_LONG",0)
            elif regime=="REPAIR" and strategy not in {"STRONG_PULLBACK","SECTOR_RESONANCE"}: decision=DeploymentDecision("REMOVED_BY_REGIME","REPAIR_REQUIRES_PULLBACK_OR_SECTOR_LEADER",multiplier)
            elif regime=="ROTATION" and strength < float(self.config["deployment"]["minimum_sector_rank_to_survive"]): decision=DeploymentDecision("REMOVED_BY_REGIME","SECTOR_RELATIVE_STRENGTH_TOO_LOW",multiplier)
            else:
                eligible.append(row); decision=DeploymentDecision("DEPLOYABLE","REGIME_GATE_PASSED",multiplier)
            output.append((row,decision))
        allowed={id(row) for row in eligible[:maximum]}
        return [(row, decision if decision.status!="DEPLOYABLE" or id(row) in allowed else DeploymentDecision("REMOVED_BY_REGIME","REGIME_CANDIDATE_LIMIT",multiplier)) for row,decision in output]


@dataclass(frozen=True)
class ConcentrationDecision:
    status: str; reason: str; retained_rank_in_sector: int; cluster_id: str


class PortfolioConcentrationGate:
    def __init__(self, config: dict[str, Any]) -> None:self.cfg=config["concentration"]
    def apply(self, rows: list[Any], industry_strength: dict[str,float]) -> list[tuple[Any,ConcentrationDecision]]:
        ranked=sorted(rows,key=lambda r:(-float(getattr(r,"admission_ranking_score_v2",0) or 0),-industry_strength.get(getattr(r,"industry",None),0),getattr(r,"quant_rank",999999) or 999999))
        total=len(ranked); ratio_cap=max(1,int(total*float(self.cfg["maximum_industry_pool_ratio"]))) if total else 0
        cap=min(int(self.cfg["maximum_per_sw_l1_industry"]),int(self.cfg["maximum_per_high_correlation_cluster"]),ratio_cap)
        counts={}; output=[]
        for row in ranked:
            industry=getattr(row,"industry",None) or "UNKNOWN"; rank=counts.get(industry,0)+1; counts[industry]=rank
            if rank<=cap: decision=ConcentrationDecision("RETAINED","WITHIN_INDUSTRY_AND_CLUSTER_LIMIT",rank,industry)
            else: decision=ConcentrationDecision("SECTOR_CROWDING_REVIEW","INDUSTRY_OR_CORRELATION_CLUSTER_LIMIT",rank,industry)
            output.append((row,decision))
        return output


@dataclass(frozen=True)
class TriggerDecision:
    status: str; reasons: list[str]; scores: dict[str,float|None]


class IntradayEntryTriggerEngine:
    def __init__(self, config: dict[str, Any]) -> None:self.cfg=config["trigger"]
    def evaluate(self, *, strategy_id: str, regime: str, bars: list[dict[str,Any]], sector_return: float|None=None) -> TriggerDecision:
        if not bars:return TriggerDecision("DATA_INSUFFICIENT",["MINUTE_BARS_NOT_AVAILABLE"],{})
        if bool(bars[-1].get("expired")):
            return TriggerDecision("EXPIRED", ["ENTRY_WINDOW_EXPIRED"], {})
        last=bars[-1]; price=_num(last.get("close")); vwap=_num(last.get("vwap")); volume_ratio=_num(last.get("volume_ratio")); gap=_num(bars[0].get("gap_percent")); stock_return=_num(last.get("change_percent")); minutes=len(bars)
        scores={"price_vs_vwap":None if price is None or not vwap else price/vwap-1,"intraday_trend":stock_return,"volume_confirmation":volume_ratio,"sector_relative_strength":None if stock_return is None or sector_return is None else stock_return-sector_return,"opening_gap_risk":gap,"market_regime_stability":100.0 if regime in {"RISK_ON","ROTATION"} else 50.0 if regime=="REPAIR" else 0.0}
        reasons=[]
        if regime in {"RISK_OFF","CRASH"}:reasons.append("REGIME_BLOCKS_ENTRY")
        if regime=="REPAIR" and minutes<int(self.cfg["repair_confirmation_minutes"]):reasons.append("REPAIR_CONFIRMATION_WINDOW_NOT_MET")
        if gap is not None and gap>float(self.cfg["maximum_opening_gap_percent"]):reasons.append("OPENING_GAP_RISK")
        if price is None or vwap is None:reasons.append("VWAP_DATA_MISSING")
        elif price<vwap:reasons.append("PRICE_BELOW_VWAP")
        elif (price/vwap-1)*100>float(self.cfg["maximum_price_above_vwap_percent"]):reasons.append("PRICE_TOO_FAR_ABOVE_VWAP")
        if volume_ratio is None or volume_ratio<float(self.cfg["minimum_volume_ratio"]):reasons.append("VOLUME_NOT_CONFIRMED")
        if scores["sector_relative_strength"] is None or scores["sector_relative_strength"]<float(self.cfg["minimum_sector_relative_strength_percent"]):reasons.append("SECTOR_RELATIVE_STRENGTH_NOT_CONFIRMED")
        if strategy_id=="TREND_BREAKOUT" and regime=="REPAIR":reasons.append("BREAKOUT_DISABLED_IN_REPAIR")
        hard_rejection = any(reason in {"REGIME_BLOCKS_ENTRY", "OPENING_GAP_RISK"} for reason in reasons)
        status = "ENTRY_TRIGGERED" if not reasons else "TRIGGER_REJECTED" if hard_rejection else "WAITING_TRIGGER"
        return TriggerDecision(status,reasons,scores)


def _num(value):
    try:return float(value) if value is not None else None
    except (TypeError,ValueError):return None
