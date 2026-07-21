from __future__ import annotations

from copy import deepcopy

import pytest

from midday.full_a_radar import (
    full_a_breadth, industry_state, percentile_scores, score_full_a,
    stable_hash, validate_radar_config, weighted_available,
)


CONFIG={
    "version":"MIDDAY_FULL_A_RADAR_V1","winsor_lower_quantile":.01,"winsor_upper_quantile":.99,
    "weights":{"baseline_quant":.4,"morning_relative_strength":.2,"morning_volume_price":.15,"sector_resonance":.15,"opening_risk_quality":.1},
    "relative_strength_weights":{"stock_return_percentile":.35,"industry_alpha_percentile":.3,"market_alpha_percentile":.2,"morning_close_location":.15},
    "volume_price_weights":{"morning_amount_ratio":.3,"turnover_strength":.25,"price_volume_consistency":.25,"morning_trend_efficiency":.2},
    "sector_weights":{"industry_return_rank":.4,"industry_breadth":.3,"stock_rank_within_industry":.3},
}


def row(code,change,industry="A",baseline=60,amount_ratio=1,hard=False):
    return {"stock_code":code,"stock_name":code,"industry":industry,"baseline_quant_score":baseline,"change_pct_to_cutoff":change,"morning_amount_ratio":amount_ratio,"turnover_rate_to_cutoff":None,"session_open":10,"session_high":11,"session_low":9,"close_at_cutoff":10*(1+change),"amplitude_to_cutoff":.2,"opening_gap_pct":0,"high_to_close_drawdown":10*(1+change)/11-1,"data_quality":"VALID_EXACT","hard_block":hard,"hard_block_score_cap":0,"risk_flags":[]}


def test_radar_weights_sum_to_one():validate_radar_config(CONFIG)
def test_invalid_radar_weight_sum_fails():
    config=deepcopy(CONFIG);config["weights"]["baseline_quant"]=.5
    with pytest.raises(ValueError,match="WEIGHT_SUM"):validate_radar_config(config)
def test_invalid_winsor_range_fails():
    config=deepcopy(CONFIG);config["winsor_lower_quantile"]=.99
    with pytest.raises(ValueError,match="WINSOR"):validate_radar_config(config)
def test_winsorize_limits_extreme_values():
    values=percentile_scores({"a":-1000,"b":1,"c":2,"d":1000},.25,.75)
    assert values["a"]["winsorized_value"]>-1000 and values["d"]["winsorized_value"]<1000
def test_percentiles_are_zero_to_one_hundred():
    values=percentile_scores({str(i):float(i) for i in range(20)})
    assert min(v["normalized_value"] for v in values.values())==0 and max(v["normalized_value"] for v in values.values())==100
def test_equal_values_receive_equal_percentile():
    values=percentile_scores({"a":1,"b":1,"c":2})
    assert values["a"]["percentile"]==values["b"]["percentile"]
def test_missing_component_reweights_available_values():
    score,effective=weighted_available({"a":100,"b":None},{"a":.4,"b":.6})
    assert score==100 and effective=={"a":1}
def test_all_missing_components_return_none():assert weighted_available({"a":None},{"a":1})==(None,{})
def test_hard_block_cannot_be_offset_by_high_score():
    scored,_=score_full_a([row("1",.1,baseline=100,hard=True),row("2",0)],CONFIG)
    blocked=next(item for item in scored if item["stock_code"]=="1")
    assert blocked["midday_radar_score"]==0
def test_stable_sort_uses_stock_code_last():
    scored,_=score_full_a([row("000002",0),row("000001",0)],CONFIG)
    assert [item["stock_code"] for item in scored]==["000001","000002"]
def test_top200_is_deterministic():
    scored,_=score_full_a([row(f"{i:06d}",(i%31)/1000,industry=str(i%10)) for i in range(250)],CONFIG)
    assert len(scored[:200])==200 and scored==score_full_a(list(reversed([row(f"{i:06d}",(i%31)/1000,industry=str(i%10)) for i in range(250)])),CONFIG)[0]
def test_full_a_universe_is_not_top100():
    scored,_=score_full_a([row(f"{i:06d}",0,industry=str(i%20)) for i in range(1201)],CONFIG)
    assert len(scored)>1000
def test_full_a_breadth_counts_signs():
    value=full_a_breadth([row("1",.01),row("2",-.01),row("3",0)],3,.95)
    assert (value["positive_count"],value["negative_count"],value["flat_count"])==(1,1,1)
def test_full_a_breadth_reports_full_scope_at_threshold():assert full_a_breadth([row(str(i),0) for i in range(95)],100,.95)["scope"]=="FULL_A_MARKET_BREADTH"
def test_partial_breadth_does_not_claim_full_a():assert full_a_breadth([row(str(i),0) for i in range(94)],100,.95)["scope"]=="PARTIAL_MARKET_BREADTH"
def test_industry_state_excludes_unknown_tradable_sector():
    values=industry_state([row("1",.01,"UNKNOWN"),row("2",.02,"A")])
    assert [item["sector_name"] for item in values]==["A"]
def test_industry_state_orders_strongest_first():
    values=industry_state([row("1",.01,"A"),row("2",.03,"B")])
    assert values[0]["sector_name"]=="B" and values[0]["industry_rank"]==1
def test_sector_resonance_penalizes_industry_laggard():
    scored,_=score_full_a([row("1",.05,"A"),row("2",-.02,"A"),row("3",.01,"B")],CONFIG)
    by={item["stock_code"]:item for item in scored};assert by["1"]["sector_resonance"]>by["2"]["sector_resonance"]
def test_score_range_is_zero_to_one_hundred():
    scored,_=score_full_a([row(str(i),i/1000) for i in range(-10,11)],CONFIG)
    assert all(0<=item["midday_radar_score"]<=100 for item in scored)
def test_score_detail_keeps_raw_normalized_and_effective_weights():
    scored,_=score_full_a([row("1",.01),row("2",-.01)],CONFIG);detail=scored[0]["score_detail"]
    assert {"raw_value","winsorized_value","percentile","normalized_value"}<=set(detail["normalized"]["return"])
    assert abs(sum(detail["effective_weights"].values())-1)<1e-9
def test_stock_code_never_enters_score_formula():
    left=score_full_a([row("000001",.01)],CONFIG)[0][0]["midday_radar_score"];right=score_full_a([row("999999",.01)],CONFIG)[0][0]["midday_radar_score"]
    assert left==right
def test_hash_is_order_stable_for_mappings():assert stable_hash({"a":1,"b":2})==stable_hash({"b":2,"a":1})
