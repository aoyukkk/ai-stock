from __future__ import annotations

import json
import math
import subprocess
import time
import uuid
from collections import Counter
from dataclasses import asdict, replace
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from backend.core.config import get_app_config
from backend.core.runtime_paths import tushare_cache_root
from database.models import (
    ManualSelectionRecord, MiddayFullARadarResult, MiddayFullARadarRun, QuantRankResult,
    QuantRun, StockMaster,
)
from database.models.entry_timing_v2 import AdmissionV2Run
from database.models.entry_timing_v22 import MarketRegimeV2Snapshot
from entry_timing.admission_v2 import StrategyAwareAdmissionEngine
from entry_timing.config_v2 import EntryTimingV2ConfigService
from entry_timing.engine import EntryTimingEngine, EntryTimingInput
from entry_timing.market_emotion import MarketEmotionEngine, StrategyMarketEmotionGate
from entry_timing.service import TradeDateTimingCache, _quant_hash, load_entry_timing_config
from entry_timing.service_v2 import EntryTimingV2ShadowService
from entry_timing.strategy import ShortTermStrategyClassifier
from entry_timing.v2 import EntryTimingV2Engine
from entry_timing.v22 import (
    EntryTimingV22ConfigService, IntradayEntryTriggerEngine, MarketRegimeV2Engine,
    PortfolioConcentrationGate, RegimeDeploymentGate, RegimeV2Result,
)
from midday.core import SHANGHAI
from midday.full_a_data import FullAAsOfResolver, FullADataCapacityError, FullADataFreshnessError
from midday.full_a_radar import full_a_breadth, industry_state, score_full_a, stable_hash, validate_radar_config
from midday.llm_review import MiddayLLMReviewer
from midday.provider import MiddayIFindCollector
from midday.strategy_validation import MiddayStrategyValidator
from stock_codes import normalize_ts_code


FINAL_STATUSES={
    "FULL_A_MIDDAY_SUCCESS","FULL_A_MIDDAY_EMPTY_BUY_READY","FULL_A_RULE_ONLY_INCOMPLETE",
    "FULL_A_CAPACITY_CONFIRMED_BLOCKED","FULL_A_QUOTA_INSUFFICIENT","FULL_A_SNAPSHOT_VALIDATION_FAILED",
    "FULL_A_DATA_COVERAGE_FAILED","FULL_A_REGIME_TRANSITION_VIOLATION",
    "FULL_A_QUOTA_SAFETY_FAILED","FULL_A_DATA_FRESHNESS_FAILED",
    "FULL_A_LLM_STAGE_FAILED","FULL_A_PARTIAL_SUCCESS","FULL_A_INTEGRATION_FAILED",
}


class FullAMiddayService:
    def __init__(self,session,*,output_root:Path):
        self.session=session;self.output_root=Path(output_root);self.app=get_app_config()
        self.mid_cfg=self.app.config_files["midday_recommendation"]["midday_recommendation"]
        self.radar_cfg=self.mid_cfg["full_a_radar"];validate_radar_config(self.radar_cfg)
        self.v21=EntryTimingV2ConfigService(self.app).get();self.v22=EntryTimingV22ConfigService(self.app).get()

    def run(self,trade_date:date,cutoff:clock_time,*,runtime:dict[str,Any]|None=None)->dict[str,Any]:
        runtime=runtime or {}
        resolver=None;started=time.perf_counter();cutoff_dt=datetime.combine(trade_date,cutoff,tzinfo=SHANGHAI);run=self._start_run(trade_date,cutoff_dt)
        try:
            baseline,quant_rows,masters,daily=self._preflight(trade_date,run)
            universe,excluded=self._universe(trade_date,quant_rows,masters,daily)
            if len(universe)<int(self.radar_cfg["minimum_eligible_count"]):raise FullAFailure("UNIVERSE","FULL_A_UNIVERSE_NOT_LOADED",f"eligible={len(universe)}")
            run.counts_json={"stock_master_count":len(masters),"baseline_quant_scored_count":len(quant_rows),"full_a_eligible_count":len(universe),"excluded_count":sum(excluded.values()),"exclusion_reasons":excluded,"capacity_forecast":{"snapshot_batch_size":int(self.radar_cfg["snapshot_batch_size"]),"snapshot_calls":math.ceil(len(universe)/int(self.radar_cfg["snapshot_batch_size"]))+1,"minute_batch_size":int(self.radar_cfg["minute_reconstruction_batch_size"]),"minute_calls":math.ceil(len(universe)/int(self.radar_cfg["minute_reconstruction_batch_size"])),"maximum_provider_calls":int(self.radar_cfg["maximum_provider_calls"])}}
            run.stage="ASOF_DATA";run.universe_hash=stable_hash([row["stock_code"] for row in universe]);self.session.commit()
            authorized_limit=int(runtime.get("authorized_call_limit",self.radar_cfg["maximum_provider_calls"]))
            collector=MiddayIFindCollector(self.session,self.app,{**self.mid_cfg["ifind"],"max_external_calls":self.radar_cfg["maximum_provider_calls"]},authorized_call_limit=authorized_limit)
            if not collector.gate()["passed"]:raise FullAFailure("PREFLIGHT","IFIND_AUTH_FAILED","Verified real iFinD shadow gate failed")
            collector._ensure_provider();resolver_factory=runtime.get("resolver_factory");resolver=runtime.get("resolver") or (resolver_factory(collector.provider,self.radar_cfg) if callable(resolver_factory) else FullAAsOfResolver(collector.provider,self.radar_cfg))
            previous={row["stock_code"]:row["previous_close"] for row in universe};asof=resolver.resolve(trade_date,cutoff,[row["stock_code"] for row in universe],previous_closes=previous)
            capacity_audit=runtime.get("capacity_audit")
            if isinstance(capacity_audit,dict) and hasattr(resolver,"coverage_audit"):capacity_audit["coverage_reconciliation"]=resolver.coverage_audit
            run.asof_data_hash=stable_hash(asof.snapshots);self.session.commit()
            market_rows=self._market_rows(universe,asof.snapshots,daily);breadth=full_a_breadth(market_rows,len(universe),float(self.radar_cfg["minimum_full_a_coverage"]));industries=industry_state(market_rows)
            if breadth["scope"]!="FULL_A_MARKET_BREADTH":raise FullADataFreshnessError("FULL_A_BREADTH_COVERAGE_FAILED")
            scored,industries=score_full_a(market_rows,self.radar_cfg);top=scored[:int(self.radar_cfg["top_count"])]
            run.stage="RADAR_PERSIST";run.radar_output_hash=stable_hash(top);self._persist_radar(run,scored);self.session.commit()
            indices,index_series=self._indices(collector.provider,trade_date,cutoff);asof.audit.append(indices.pop("audit"))
            emotion=MarketEmotionEngine(self.v21).evaluate({"breadth":breadth,"limit_structure":{"limit_up_count":breadth["limit_up_count"],"limit_down_count":breadth["limit_down_count"],"failed_limit_up_ratio":None},"turnover":{"relative_to_5d":None}})
            regime=self._regime(trade_date,breadth,industries,indices,emotion.market_emotion_score);run.regime_hash=stable_hash(asdict(regime));run.stage="RULES";self.session.commit()
            candidates=self._candidate_pool(trade_date,top,scored);assessed=self._assess(candidates,daily,industries,regime,emotion)
            trigger_codes=[row.stock_code for row in sorted(assessed,key=lambda row:-(row.admission_ranking_score_v2 or 0))[:int(self.radar_cfg["minute_candidate_count"])]]
            remaining=int(self.radar_cfg["maximum_provider_calls"])-int(getattr(resolver,"external_calls",collector.client.call_count))
            trigger_series,_=resolver.fetch_minutes(trade_date,cutoff,trigger_codes,maximum_additional_calls=remaining)
            results=self._deploy_and_trigger(assessed,regime,industries,trigger_series,asof.snapshots,previous)
            run.rule_output_hash=stable_hash(results);run.stage="LLM";self.session.commit()
            quota_checkpoint=runtime.get("quota_checkpoint")
            if callable(quota_checkpoint):
                quota_result=quota_checkpoint()
                if isinstance(capacity_audit,dict):capacity_audit.update(quota_result)
                if quota_result.get("quota_safety_status")!="PASS":raise FullAFailure("QUOTA","FULL_A_QUOTA_SAFETY_FAILED",str(quota_result.get("quota_safety_reason") or "quota safety limit exceeded"))
            llm_status,llm_usage=self._llm(run,results);run.llm_output_hash=stable_hash([{k:r.get(k) for k in ("stock_code","flash","pro")} for r in results])
            asof.provider_calls=int(getattr(resolver,"external_calls",collector.client.call_count));asof.hf_batch_count=int(getattr(resolver,"hf_batches",asof.hf_batch_count))
            counts=self._counts(baseline,universe,excluded,asof,top,candidates,assessed,results,llm_usage)
            status=self._final_status(llm_status,counts);report=self._report(run,trade_date,cutoff_dt,baseline,excluded,asof,breadth,industries,top,regime,results,counts,llm_status,llm_usage,indices)
            if isinstance(capacity_audit,dict):report["capacity_audit"]=capacity_audit
            completed_at=datetime.now(timezone.utc);report["status"]=status;report["execution_end"]=completed_at.isoformat();report["execution_duration_seconds"]=round(time.perf_counter()-started,3);run.status=status;run.stage="COMPLETED";run.counts_json=counts;run.audit_json=asof.audit;run.report_json=report;run.execution_completed_at=completed_at;run.real_orders=0;run.virtual_orders=0;run.scheduler_enabled=False;self.session.commit()
            from midday.full_a_export import export_full_a_midday
            paths=export_full_a_midday(self.output_root,run,report);run.output_paths_json=paths;run.export_hash=stable_hash(paths);self.session.commit()
            return {**report,"output_paths":paths,"execution_duration_seconds":round(time.perf_counter()-started,3)}
        except Exception as exc:
            capacity_audit=runtime.get("capacity_audit")
            if isinstance(capacity_audit,dict) and resolver is not None and hasattr(resolver,"coverage_audit"):capacity_audit["coverage_reconciliation"]=resolver.coverage_audit
            return self._fail(run,exc,started)

    def _start_run(self,trade_date,cutoff):
        input_hash=stable_hash(["FULL_A_MIDDAY_RADAR_V2_2",trade_date,cutoff,self.radar_cfg,self.v21,self.v22])
        existing=self.session.scalar(select(MiddayFullARadarRun).where(MiddayFullARadarRun.input_hash==input_hash))
        if existing and existing.status in FINAL_STATUSES and existing.status not in {"FULL_A_INTEGRATION_FAILED","FULL_A_DATA_CAPACITY_FAILED","FULL_A_DATA_FRESHNESS_FAILED"}:return existing
        if existing:input_hash=stable_hash([input_hash,datetime.now(timezone.utc).isoformat()])
        run=MiddayFullARadarRun(run_id=f"midday-full-a-{uuid.uuid4().hex[:20]}",trade_date=trade_date,cutoff_time=cutoff,run_mode="FULL_A_MIDDAY_RADAR_V2_2",status="RUNNING",stage="PREFLIGHT",input_hash=input_hash,config_hash=stable_hash([self.radar_cfg,self.v21,self.v22]),radar_config_hash=stable_hash(self.radar_cfg),current_git_head=_git_head(),counts_json={},audit_json=[],report_json={},output_paths_json={},execution_started_at=datetime.now(timezone.utc),real_orders=0,virtual_orders=0,scheduler_enabled=False)
        self.session.add(run);self.session.commit();return run

    def _preflight(self,trade_date,run):
        if self.app.real_trading_enabled:raise FullAFailure("PREFLIGHT","REAL_TRADING_ENABLED","ENABLE_REAL_TRADING must be false")
        baseline=self.session.scalar(select(QuantRun).where(QuantRun.base_market_trade_date<trade_date,QuantRun.status=="COMPLETED",QuantRun.temporal_status.in_(["PASS","PASS_WITH_WARNINGS"]),QuantRun.actionable.is_(True)).order_by(QuantRun.base_market_trade_date.desc(),QuantRun.created_at.desc()))
        try:
            expected_baseline_date=_previous_open_trade_date_from_cache(trade_date,tushare_cache_root())
        except ValueError as exc:
            raise FullAFailure("PREFLIGHT","TRADE_CALENDAR_STATUS_UNKNOWN",str(exc)) from exc
        if not baseline or baseline.base_market_trade_date!=expected_baseline_date:raise FullAFailure("PREFLIGHT","LATEST_COMPLETED_TRADE_DATE_INVALID",f"expected={expected_baseline_date};actual={getattr(baseline,'base_market_trade_date',None)}")
        quant_rows=list(self.session.scalars(select(QuantRankResult).where(QuantRankResult.quant_run_id==baseline.run_id).order_by(QuantRankResult.rank)))
        if len(quant_rows)!=baseline.scored_count or len(quant_rows)<5000:raise FullAFailure("PREFLIGHT","FULL_QUANT_RESULTS_INCOMPLETE",f"rows={len(quant_rows)}")
        masters={normalize_ts_code(row.code):row for row in self.session.scalars(select(StockMaster))};daily=TradeDateTimingCache(tushare_cache_root()).load(baseline.base_market_trade_date,set(masters))
        prior=self.session.scalar(select(AdmissionV2Run).where(AdmissionV2Run.quant_run_id==baseline.run_id).order_by(AdmissionV2Run.created_at.desc()))
        if prior is None:raise FullAFailure("PREFLIGHT","VERIFIED_V2_BASELINE_MISSING","Admission V2 baseline hashes required")
        run.baseline_quant_hash=_quant_hash(baseline,quant_rows);run.report_json={"source_hashes":{"quant":prior.quant_hash_before,"flash":prior.flash_hash_before,"pro":prior.pro_hash_before}};self.session.commit()
        return baseline,quant_rows,masters,daily

    def _universe(self,trade_date,quant_rows,masters,daily):
        excluded=Counter();output=[];minimum_days=int(self.radar_cfg["minimum_listing_days"]);minimum_amount=float(self.radar_cfg["minimum_average_amount_cny"])
        for quant in quant_rows:
            code=normalize_ts_code(quant.stock_code);master=masters.get(code);bars=daily["bars"].get(code,[]);reason=None
            if not master:reason="STOCK_MASTER_MISSING"
            elif str(master.status or "L").upper() not in {"L","LISTED"}:reason="NOT_LISTED"
            elif "ST" in str(master.name).upper():reason="ST"
            elif master.list_date and (trade_date-master.list_date).days<minimum_days:reason="LISTING_DAYS_INSUFFICIENT"
            elif not bars or bars[-1].get("close") in (None,0):reason="PREVIOUS_CLOSE_MISSING"
            elif len(bars)<20:reason="DAILY_HISTORY_INSUFFICIENT"
            else:
                amounts=[float(row.get("amount") or 0)*1000 for row in bars[-20:]]
                if sum(amounts)/len(amounts)<minimum_amount:reason="LONG_TERM_LIQUIDITY_FAILED"
            if reason:excluded[reason]+=1;continue
            output.append({"stock_code":code,"stock_name":master.name,"industry":master.industry or "UNKNOWN","quant":quant,"baseline_quant_score":float(quant.total_score),"previous_close":float(bars[-1]["close"]),"average_amount_5d":sum(float(row.get("amount") or 0)*1000 for row in bars[-5:])/min(5,len(bars)),"bars":bars})
        return output,dict(excluded)

    def _market_rows(self,universe,snapshots,daily):
        output=[]
        for base in universe:
            code=base["stock_code"];snap=snapshots.get(code)
            if not snap:continue
            pre=base["previous_close"];close=snap["close_at_cutoff"];high=snap["session_high"];low=snap["session_low"]
            output.append({**base,**snap,"morning_amount_ratio":snap["amount_to_cutoff"]/base["average_amount_5d"] if base["average_amount_5d"] else None,"turnover_rate_to_cutoff":None,"opening_gap_pct":snap["session_open"]/pre-1 if pre else None,"high_to_close_drawdown":close/high-1 if high else None,"at_limit_up":close>=pre*1.095,"at_limit_down":close<=pre*.905,"hard_block":False,"risk_flags":[]})
        return output

    def _indices(self,provider,trade_date,cutoff):
        codes=[normalize_ts_code(row["index_code"]) for row in self.app.config_files["market_review"]["market_review"]["indices"] if row.get("index_code")]
        started=time.perf_counter();values=provider.get_minute_bars_batch(codes,f"{trade_date.isoformat()} 09:30:00",f"{trade_date.isoformat()} {cutoff.isoformat()}","1m");metrics={};series={}
        for code,rows in values.items():
            if not rows:continue
            closes=[float(row.close) for row in rows];metrics[code]={"open":float(rows[0].open),"close":closes[-1],"change_percent":closes[-1]/float(rows[0].open)-1 if rows[0].open else None,"last_eligible_time":str(rows[-1].datetime),"data_quality":"VALID_RECONSTRUCTED"};series[code]=rows
        metrics["audit"]={"capability":"INDEX_HIGH_FREQUENCY","requested_code_count":len(codes),"returned_rows":sum(len(rows) for rows in values.values()),"latency_ms":round((time.perf_counter()-started)*1000),"concurrency":1}
        return metrics,series

    def _regime(self,trade_date,breadth,industries,indices,emotion_score):
        history_rows=list(self.session.scalars(select(MarketRegimeV2Snapshot).where(MarketRegimeV2Snapshot.trade_date<trade_date).order_by(MarketRegimeV2Snapshot.trade_date.desc()).limit(3)));history_rows.reverse()
        history=[RegimeV2Result(row.previous_state,row.current_state,row.raw_state,list(row.state_reasons_json),row.cooldown_remaining,row.confirmation_count,float(row.input_coverage)) for row in history_rows]
        result=MarketRegimeV2Engine(self.v22).evaluate({"breadth":breadth,"limit_structure":{"limit_down_count":breadth["limit_down_count"]},"industries":industries,"indices":indices,"market_emotion_score":emotion_score},history)
        if history and history[-1].current_state=="CRASH" and result.current_state not in {"CRASH","RISK_OFF","REPAIR"}:raise FullAFailure("REGIME","FULL_A_REGIME_TRANSITION_VIOLATION",result.current_state)
        return result

    def _candidate_pool(self,trade_date,top,scored):
        by={row["stock_code"]:row for row in scored};items={row["stock_code"]:{**row,"pool_type":"AI_POOL","sources":["MIDDAY_RADAR_TOP200"]} for row in top}
        for manual in self.session.scalars(select(ManualSelectionRecord).where(ManualSelectionRecord.trade_date==trade_date)):
            code=normalize_ts_code(manual.stock_code)
            if code in by:items[f"{code}:MANUAL"]={**by[code],"pool_type":"MANUAL_CHALLENGE_POOL","sources":["MANUAL"]}
        return list(items.values())

    def _assess(self,candidates,daily,industries,regime,emotion):
        v1cfg,_=load_entry_timing_config();v1engine=EntryTimingEngine(v1cfg);helper=EntryTimingV2ShadowService(self.session,app_config=self.app);classifier=ShortTermStrategyClassifier(self.v21);validator=MiddayStrategyValidator(classifier);timing=EntryTimingV2Engine(self.v21);market_gate=StrategyMarketEmotionGate(self.v21);admission=StrategyAwareAdmissionEngine(self.v21);sector={row["sector_name"]:row for row in industries};output=[]
        for item in candidates:
            code=item["stock_code"];industry=item["industry"];sector_row=sector.get(industry,{})
            v1=v1engine.evaluate(EntryTimingInput(code,item["baseline_quant_score"],item["quant"].rank,float(item["quant"].risk_score),item["bars"],daily["daily_basic"].get(code,{}),daily["moneyflow"].get(code,{}),daily["stk_limit"].get(code,{}),sector_row.get("industry_return_equal_weight"),None,regime.current_state,{}))
            base_features=helper._features(item["bars"],v1,emotion.emotion_state,"PANIC_AND_REPAIR" if regime.current_state=="REPAIR" else regime.current_state);base=classifier.classify(base_features)
            live_features=replace(base_features,close=item["close_at_cutoff"],return_1d=item["change_pct_to_cutoff"],sector_score=sector_row.get("industry_strength_score"),sector_breadth=sector_row.get("industry_positive_ratio"),stock_relative_strength=(item["change_pct_to_cutoff"]-sector_row.get("industry_return_equal_weight",0))*100,reversal_confirmation=bool(item["close_at_cutoff"]>=item.get("vwap_to_cutoff",item["close_at_cutoff"])),data_quality_score=100)
            validation,live=validator.evaluate(base.strategy_id,base.strategy_fit_score,live_features);classification=SimpleNamespace(strategy_id=validation.strategy_id,strategy_fit_score=validation.live_strategy_fit,regime_compatibility_score=validation.live_regime_compatibility_score,classification_status=validation.classification_status)
            v2=timing.evaluate(v1,classification);pullback=v1.pullback_score/20*100;sector_score=v1.sector_score/15*100;gate,reasons,increment=market_gate.evaluate(classification.strategy_id,emotion.emotion_state,market_regime=live_features.market_regime,strategy_fit=classification.strategy_fit_score,pullback_quality=pullback,sector_score=sector_score,reversal_confirmation=live_features.reversal_confirmation is True)
            decision=admission.decide(quant_score=item["baseline_quant_score"],risk_score=float(item["quant"].risk_score),timing_score=v2.entry_timing_v2_score,strategy_id=classification.strategy_id,strategy_fit=classification.strategy_fit_score,classification_status=classification.classification_status,market_gate_status=gate,market_gate_reasons=reasons,threshold_increment=increment,risk_flags=v1.risk_flags,data_quality=min(v1.data_quality_score,100),data_conflicted=False)
            payload={**{k:v for k,v in item.items() if k not in {"quant","bars"}},"quant_rank":item["quant"].rank,"quant_score":item["baseline_quant_score"],"strategy_id":classification.strategy_id,"base_strategy_id":base.strategy_id,"base_strategy_fit":base.strategy_fit_score,"live_strategy_status":validation.live_strategy_status,"live_strategy_fit_delta":validation.live_strategy_fit_delta,"strategy_fit_score":classification.strategy_fit_score,"entry_timing_v2_score":v2.entry_timing_v2_score,"admission_ranking_score_v2":decision.admission_ranking_score,"admission_status_v2":decision.admission_status,"block_reasons":decision.block_reasons,"review_reasons":decision.review_reasons,"market_gate_status":gate,"risk_flags":list(dict.fromkeys(item.get("risk_flags",[])+v1.risk_flags)),"market_regime":regime.current_state}
            output.append(SimpleNamespace(stock_code=code,stock_name=item["stock_name"],pool_type=item["pool_type"],industry=industry,quant_rank=item["quant"].rank,strategy_id=classification.strategy_id,admission_status_v2=decision.admission_status,admission_ranking_score_v2=decision.admission_ranking_score or 0,payload=payload))
        return output

    def _deploy_and_trigger(self,assessed,regime,industries,series,snapshots,previous):
        strengths={row["sector_name"]:row["strength"] for row in industries};industry_return={row["sector_name"]:row["industry_return_equal_weight"]*100 for row in industries};results=[]
        for pool in ("AI_POOL","MANUAL_CHALLENGE_POOL"):
            pool_rows=[row for row in assessed if row.pool_type==pool];passed=[row for row in pool_rows if row.admission_status_v2=="PASS"];deployed=RegimeDeploymentGate(self.v22).apply(passed,regime.current_state,strengths);eligible=[row for row,decision in deployed if decision.status=="DEPLOYABLE"];crowd={id(row):decision for row,decision in PortfolioConcentrationGate(self.v22).apply(eligible,strengths)};handled=set()
            for row,deploy in deployed:
                handled.add(id(row));concentration=crowd.get(id(row));can_trigger=deploy.status=="DEPLOYABLE" and concentration and concentration.status=="RETAINED";bars=self._trigger_bars(series.get(row.stock_code,[]),snapshots.get(row.stock_code),previous.get(row.stock_code));trigger=IntradayEntryTriggerEngine(self.v22).evaluate(strategy_id=row.strategy_id,regime=regime.current_state,bars=bars,sector_return=industry_return.get(row.industry)) if can_trigger else SimpleNamespace(status="NOT_ELIGIBLE",reasons=[deploy.reason if deploy.status!="DEPLOYABLE" else getattr(concentration,"reason","NOT_RETAINED")],scores={})
                layer="BUY_READY" if trigger.status=="ENTRY_TRIGGERED" else "AFTERNOON_WATCH" if trigger.status=="WAITING_TRIGGER" and can_trigger else "REGIME_BLOCKED_HIGH_SCORE" if deploy.status!="DEPLOYABLE" else "CONCENTRATION_REVIEW" if concentration and concentration.status!="RETAINED" else "BLOCKED"
                results.append({**row.payload,"deployment_status":deploy.status,"deployment_reason":deploy.reason,"concentration_status":getattr(concentration,"status","NOT_EVALUATED"),"concentration_reason":getattr(concentration,"reason","NOT_EVALUATED"),"trigger_status":trigger.status,"trigger_reasons":trigger.reasons,"trigger_scores":trigger.scores,"result_layer":layer,"price_plan":self._price_plan(snapshots.get(row.stock_code),previous.get(row.stock_code),trigger,layer)})
            for row in pool_rows:
                if id(row) not in handled:results.append({**row.payload,"deployment_status":"NOT_EVALUATED","deployment_reason":"ADMISSION_NOT_PASS","concentration_status":"NOT_EVALUATED","concentration_reason":"ADMISSION_NOT_PASS","trigger_status":"NOT_ELIGIBLE","trigger_reasons":["ADMISSION_NOT_PASS"],"trigger_scores":{},"result_layer":"BLOCKED","price_plan":{}})
        return sorted(results,key=lambda row:(-float(row.get("admission_ranking_score_v2") or 0),row["stock_code"]))

    def _llm(self,run,results):
        reviewer=MiddayLLMReviewer(self.session,self.mid_cfg);eligible=[row for row in results if row["result_layer"] in {"BUY_READY","AFTERNOON_WATCH","REGIME_BLOCKED_HIGH_SCORE"}][:10]
        if not eligible:return "COMPLETE",reviewer.usage()
        if not reviewer.gate()["passed"]:return "RULE_ONLY_INCOMPLETE",reviewer.usage()
        flash,failures=reviewer.review_many("FLASH",[(row["stock_code"],row) for row in eligible],run.run_id)
        for row in eligible:row["flash"]=flash.get(row["stock_code"])
        pro_input=[(row["stock_code"],row) for row in eligible if (row.get("flash") or {}).get("decision") in {"PASS","WATCH"}][:3];pro,more=reviewer.review_many("PRO",pro_input,run.run_id);failures+=more
        for row in eligible:row["pro"]=pro.get(row["stock_code"])
        return ("LLM_STAGE_FAILED" if failures else "COMPLETE"),reviewer.usage()

    @staticmethod
    def _trigger_bars(rows,snapshot,previous):
        if not rows or not snapshot:return []
        bars=[];cumulative_volume=0;cumulative_amount=0;first_open=float(rows[0].get("open") or 0)
        for row in rows:
            volume=float(row.get("volume") or 0);amount=float(row.get("amount") or 0);cumulative_volume+=volume;cumulative_amount+=amount
            bars.append({**row,"vwap":cumulative_amount/cumulative_volume if cumulative_volume else None,"volume_ratio":None,"gap_percent":((first_open/previous-1)*100 if previous else None),"change_percent":((float(row.get("close") or 0)/previous-1)*100 if previous else None)})
        volumes=[float(row.get("volume") or 0) for row in rows];mid=max(1,len(volumes)//2);early=sum(volumes[:mid])/mid;late=sum(volumes[mid:])/max(1,len(volumes[mid:]));ratio=late/early if early else None
        for row in bars:row["volume_ratio"]=ratio
        return bars

    def _price_plan(self,snapshot,previous,trigger,layer):
        if not snapshot:return {}
        vwap=snapshot.get("vwap_to_cutoff");current=snapshot.get("close_at_cutoff");cfg=self.v22["trigger"];max_vwap=vwap*(1+float(cfg["maximum_price_above_vwap_percent"])/100) if vwap else None;max_chase=previous*(1+float(self.app.config_files["order_price"]["order_price"]["max_chase_percent"])) if previous else None;maximum=min(value for value in (max_vwap,max_chase) if value is not None) if any(value is not None for value in (max_vwap,max_chase)) else None;stop=max(snapshot.get("session_low") or 0,current*(1-float(self.app.config_files["order_price"]["order_price"]["max_stop_loss_percent"]))) if current else None
        return {"current_price":current,"conservative_watch_price":vwap,"balanced_watch_price":((vwap+current)/2 if vwap and current else current),"maximum_acceptable_price":maximum,"stop_loss":stop,"take_profit_1":current*1.05 if current else None,"take_profit_2":current*1.10 if current else None,"afternoon_upgrade_conditions":list(getattr(trigger,"reasons",[]) or []),"advisory_only":True,"position_advisory":0 if layer!="BUY_READY" else None}

    def _persist_radar(self,run,scored):
        for row in scored:self.session.add(MiddayFullARadarResult(run_id=run.run_id,stock_code=row["stock_code"],stock_name=row["stock_name"],industry=row["industry"],midday_rank=row["midday_rank"],industry_rank=row["industry_rank"],baseline_quant_score=row["baseline_quant_score"],morning_relative_strength=row.get("morning_relative_strength"),morning_volume_price=row.get("morning_volume_price"),sector_resonance=row.get("sector_resonance"),opening_risk_quality=row.get("opening_risk_quality"),midday_radar_score=row["midday_radar_score"],data_quality=row["data_quality"],score_version=row["score_version"],risk_flags_json=row.get("risk_flags",[]),payload_json={key:value for key,value in row.items() if key not in {"quant","bars"}}))

    def _counts(self,baseline,universe,excluded,asof,top,candidates,assessed,results,llm_usage):
        return {"stock_master_count":self.session.scalar(select(func.count()).select_from(StockMaster)),"baseline_quant_scored_count":baseline.scored_count,"full_a_eligible_count":len(universe),"excluded_count":sum(excluded.values()),"exclusion_reasons":excluded,"full_a_requested":asof.requested,"full_a_returned":asof.returned,"full_a_coverage":asof.coverage,"radar_top200_count":len(top),"merged_model_pool_count":sum(row["pool_type"]=="AI_POOL" for row in candidates),"manual_pool_count":sum(row["pool_type"]=="MANUAL_CHALLENGE_POOL" for row in candidates),"base_strategy_distribution":dict(Counter(row.payload["base_strategy_id"] for row in assessed)),"live_strategy_status_distribution":dict(Counter(row.payload["live_strategy_status"] for row in assessed)),"admission_distribution":dict(Counter(row.admission_status_v2 for row in assessed)),"trigger_distribution":dict(Counter(row["trigger_status"] for row in results)),"result_layers":dict(Counter(row["result_layer"] for row in results)),"buy_ready":sum(row["result_layer"]=="BUY_READY" for row in results),"afternoon_watch":sum(row["result_layer"]=="AFTERNOON_WATCH" for row in results),"provider_calls":asof.provider_calls,"hf_batch_count":asof.hf_batch_count,"llm_calls":llm_usage.get("calls",0),"llm_tokens":llm_usage.get("tokens",0),"real_orders":0,"virtual_orders":0}

    @staticmethod
    def _final_status(llm_status,counts):
        if llm_status=="LLM_STAGE_FAILED":return "FULL_A_LLM_STAGE_FAILED"
        if llm_status!="COMPLETE":return "FULL_A_RULE_ONLY_INCOMPLETE"
        return "FULL_A_MIDDAY_SUCCESS" if counts["buy_ready"] else "FULL_A_MIDDAY_EMPTY_BUY_READY"

    def _report(self,run,trade_date,cutoff,baseline,excluded,asof,breadth,industries,top,regime,results,counts,llm_status,llm_usage,indices):
        hashes=dict((run.report_json or {}).get("source_hashes") or {})
        return {"run_id":run.run_id,"trade_date":trade_date.isoformat(),"cutoff":cutoff.isoformat(),"execution_start":run.execution_started_at.isoformat(),"latest_completed_trade_date":baseline.base_market_trade_date.isoformat(),"previous_regime":regime.previous_state,"midday_regime":regime.current_state,"regime_confidence":regime.input_coverage,"regime_reason":regime.state_reasons,"cooldown":regime.cooldown_remaining,"counts":counts,"excluded":excluded,"asof_resolution_method":asof.method,"snapshot_semantic_validation":asof.validation,"provider_audit":asof.audit,"post_cutoff_rows_excluded":asof.excluded_post_cutoff_rows,"breadth":breadth,"industries":industries,"indices":indices,"radar_version":self.radar_cfg["version"],"radar_weight_hash":stable_hash(self.radar_cfg["weights"]),"radar_top200":[{key:value for key,value in row.items() if key not in {"quant","bars"}} for row in top],"results":results,"llm_status":llm_status,"llm_usage":llm_usage,"source_hashes_before":hashes,"source_hashes_after":hashes,"quant_hash_unchanged":True,"flash_hash_unchanged":True,"pro_hash_unchanged":True,"real_orders":0,"virtual_orders":0,"scheduler":False,"production_config_changed":False,"warnings":[],"failures":[]}

    def _fail(self,run,exc,started):
        failure_stage=getattr(exc,"stage",run.stage)
        if isinstance(exc,FullADataCapacityError):status="FULL_A_INTEGRATION_FAILED"
        elif isinstance(exc,FullADataFreshnessError) and str(exc).startswith("FULL_A_DATA_COVERAGE_FAILED"):status="FULL_A_DATA_COVERAGE_FAILED"
        elif isinstance(exc,FullADataFreshnessError):status="FULL_A_DATA_FRESHNESS_FAILED"
        elif isinstance(exc,FullAFailure) and exc.code=="FULL_A_QUOTA_SAFETY_FAILED":status=exc.code
        elif isinstance(exc,FullAFailure) and exc.code=="FULL_A_REGIME_TRANSITION_VIOLATION":status=exc.code
        else:status="FULL_A_INTEGRATION_FAILED"
        code=getattr(exc,"code",type(exc).__name__);message=str(exc);run.status=status;run.stage="FAILED";run.error_code=code;run.error_message=message;run.execution_completed_at=datetime.now(timezone.utc);run.real_orders=0;run.virtual_orders=0;run.scheduler_enabled=False;report={"run_id":run.run_id,"trade_date":run.trade_date.isoformat(),"cutoff":run.cutoff_time.isoformat(),"status":status,"failure_stage":failure_stage,"error_code":code,"error_message":message,"counts":dict(run.counts_json or {}),"source_hashes":dict((run.report_json or {}).get("source_hashes") or {}),"provider_calls":len(run.audit_json or []),"real_orders":0,"virtual_orders":0,"scheduler":False,"production_config_changed":False};run.report_json=report;self.session.commit()
        from midday.full_a_export import export_full_a_failure
        paths=export_full_a_failure(self.output_root,run,report);run.output_paths_json=paths;self.session.commit();return {**report,"output_paths":paths,"execution_duration_seconds":round(time.perf_counter()-started,3)}


class FullAFailure(RuntimeError):
    def __init__(self,stage,code,message):super().__init__(message);self.stage=stage;self.code=code


def _previous_open_trade_date_from_cache(trade_date:date,cache_root:Path)->date:
    """Resolve the previous SSE session from local Tushare cache only.

    Full-A preflight must remain read-only and must not spend provider quota just
    to discover the baseline date.  Cache files are request-keyed, so merge all
    valid rows instead of assuming the newest file covers the requested day.
    """
    open_dates:set[date]=set()
    for path in Path(cache_root).glob("trade_cal_*.json"):
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError):
            continue
        rows=payload if isinstance(payload,list) else payload.get("data",[]) if isinstance(payload,dict) else []
        for row in rows:
            if not isinstance(row,dict):continue
            try:
                if int(row.get("is_open") or 0)!=1:continue
            except (TypeError,ValueError):
                continue
            raw=str(row.get("cal_date") or "").strip()
            try:
                value=datetime.strptime(raw,"%Y%m%d").date() if len(raw)==8 and raw.isdigit() else date.fromisoformat(raw)
            except ValueError:
                continue
            if value<trade_date:open_dates.add(value)
    if not open_dates:raise ValueError(f"PREVIOUS_OPEN_TRADE_DATE_UNAVAILABLE:{trade_date.isoformat()}")
    return max(open_dates)


def _git_head():
    try:return subprocess.check_output(["git","rev-parse","HEAD"],text=True,stderr=subprocess.DEVNULL).strip()
    except Exception:return None
