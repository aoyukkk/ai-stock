from __future__ import annotations

from types import SimpleNamespace

import pytest

from entry_timing.market_adjusted import MarketAdjustedPerformanceEvaluator
from entry_timing.v22 import EntryTimingV22ConfigService, IntradayEntryTriggerEngine, MarketRegimeV2Engine, PortfolioConcentrationGate, RegimeDeploymentGate


@pytest.fixture(scope="module")
def config():return EntryTimingV22ConfigService().get()


def _snapshot(eq,up,down,emotion,below5=50,limit_down=5,industry_positive=8):
    return {"breadth":{"equal_weight_return":eq,"advancing_ratio":up,"declining_ratio":down,"valid_count":1000,"below_5_count":below5},"limit_structure":{"limit_down_count":limit_down},"market_emotion_score":emotion,"industries":[{"change_percent":1 if i<industry_positive else -1} for i in range(10)]}


def test_crash_rebound_cooldown_and_confirmed_risk_on(config):
    engine=MarketRegimeV2Engine(config); history=[]
    history.append(engine.evaluate(_snapshot(-.04,.08,.9,20,400,150,0),history));assert history[-1].current_state=="CRASH"
    history.append(engine.evaluate(_snapshot(.02,.8,.15,80,20,2,9),history));assert history[-1].current_state=="REPAIR" and history[-1].cooldown_remaining==1
    history.append(engine.evaluate(_snapshot(.012,.7,.25,75,30,3,8),history));assert history[-1].current_state=="REPAIR"
    history.append(engine.evaluate(_snapshot(-.012,.25,.7,35,150,30,2),history));assert history[-1].current_state=="RISK_OFF"
    history.append(engine.evaluate(_snapshot(.01,.7,.2,75,30,2,8),history));assert history[-1].current_state=="REPAIR"
    history.append(engine.evaluate(_snapshot(.01,.7,.2,75,30,2,8),history));assert history[-1].current_state=="RISK_ON"


def test_single_down_day_does_not_become_crash_without_hard_trigger(config):
    engine=MarketRegimeV2Engine(config)
    prior=[engine.evaluate(_snapshot(.01,.7,.2,75),[]),engine.evaluate(_snapshot(.01,.7,.2,75),[])]
    result=engine.evaluate(_snapshot(-.003,.4,.55,55),prior)
    assert result.current_state in {"ROTATION","RISK_OFF"} and result.current_state!="CRASH"


def test_regime_gate_limits_and_blocks(config):
    rows=[SimpleNamespace(admission_ranking_score_v2=100-i,quant_rank=i,strategy_id="SECTOR_RESONANCE",industry="医药") for i in range(25)]
    gate=RegimeDeploymentGate(config)
    assert sum(d.status=="DEPLOYABLE" for _,d in gate.apply(rows,"RISK_ON",{"医药":1}))==20
    assert sum(d.status=="DEPLOYABLE" for _,d in gate.apply(rows,"ROTATION",{"医药":1}))==8
    assert sum(d.status=="DEPLOYABLE" for _,d in gate.apply(rows,"REPAIR",{"医药":1}))==3
    assert sum(d.status=="DEPLOYABLE" for _,d in gate.apply(rows,"RISK_OFF",{"医药":1}))==0
    assert sum(d.status=="DEPLOYABLE" for _,d in gate.apply(rows,"CRASH",{"医药":1}))==0


def test_concentration_uses_score_and_enforces_industry_cluster_ratio(config):
    rows=[SimpleNamespace(stock_code=code,admission_ranking_score_v2=score,quant_rank=rank,industry=industry) for code,score,rank,industry in [("000001",60,1,"A"),("999999",90,9,"A"),("000002",80,2,"A"),("000003",70,3,"B"),("000004",65,4,"B"),("000005",50,5,"C")]]
    output=PortfolioConcentrationGate(config).apply(rows,{"A":.9,"B":.7,"C":.5})
    retained=[row.stock_code for row,d in output if d.status=="RETAINED"]
    assert "999999" in retained and "000001" not in retained
    assert sum(row.industry=="A" and d.status=="RETAINED" for row,d in output)<=1


def test_intraday_trigger_vwap_sector_gap_and_repair_window(config):
    engine=IntradayEntryTriggerEngine(config)
    bars=[{"close":10.2,"vwap":10,"volume_ratio":1.5,"gap_percent":1,"change_percent":2} for _ in range(16)]
    assert engine.evaluate(strategy_id="SECTOR_RESONANCE",regime="RISK_ON",bars=bars,sector_return=1).status=="ENTRY_TRIGGERED"
    assert engine.evaluate(strategy_id="SECTOR_RESONANCE",regime="REPAIR",bars=bars[:5],sector_return=1).status=="WAITING_TRIGGER"
    risky=[{**bars[0],"gap_percent":8} for _ in range(16)]
    assert "OPENING_GAP_RISK" in engine.evaluate(strategy_id="SECTOR_RESONANCE",regime="RISK_ON",bars=risky,sector_return=1).reasons
    assert engine.evaluate(strategy_id="SECTOR_RESONANCE",regime="RISK_ON",bars=[],sector_return=1).status=="DATA_INSUFFICIENT"


def test_market_adjusted_absolute_relative_and_missing_benchmark():
    evaluator=MarketAdjustedPerformanceEvaluator()
    result=evaluator.evaluate([.02,-.01],[.01,-.02],benchmark_type="INDUSTRY",benchmark_code="医药",benchmark_quality="HIGH")
    assert result["stock_return_D1"]==pytest.approx(.02);assert result["alpha_D1"]==pytest.approx(.01);assert result["industry_alpha"] is not None
    assert result["alpha_MAE"] <= 0 and result["alpha_MFE"] >= 0
    missing=evaluator.evaluate([.02],None,benchmark_type=None,benchmark_code=None,benchmark_quality="MISSING",missing_reason="NO_VALID_BENCHMARK")
    assert missing["benchmark_return_D1"] is None and missing["alpha_D1"] is None
    rows=[{**result,"selected":True,"cumulative_return":.01},{**result,"selected":False,"cumulative_return":-.1}]
    metrics=evaluator.metrics(rows)
    assert metrics["absolute_win_rate"]==1 and metrics["relative_win_rate"]==1
    assert metrics["industry_relative_win_rate"]==1
    assert metrics["risk_avoidance_rate"]==1 and metrics["opportunity_cost"]==0


def test_v22_safety(config):
    assert config["enabled"] is False and config["shadow_only"] is True
    assert all(not config["safety"][key] for key in ("actionable","scheduler_enabled","create_orders","create_virtual_orders","real_trading_enabled","external_historical_calls_enabled","llm_calls_enabled","parameter_search_enabled"))
