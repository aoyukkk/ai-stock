from __future__ import annotations
import hashlib,json,time,uuid
from collections import Counter,defaultdict
from datetime import date,datetime,time as clock_time,timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from sqlalchemy import select

from backend.core.config import get_app_config
from backend.core.runtime_paths import tushare_cache_root
from database.models import EntryTimingResult,EntryTimingV2Result,MiddayV22Result,MiddayV22Run,StockMaster
from database.models.entry_timing_v2 import AdmissionV2Run
from database.models.entry_timing_v22 import MarketRegimeV2Snapshot
from entry_timing.admission_v2 import StrategyAwareAdmissionEngine
from entry_timing.config_v2 import EntryTimingV2ConfigService
from entry_timing.engine import EntryTimingAssessment
from entry_timing.market_emotion import MarketEmotionEngine,StrategyMarketEmotionGate
from entry_timing.service_v2 import EntryTimingV2ShadowService
from entry_timing.service import TradeDateTimingCache
from entry_timing.strategy import ShortTermStrategyClassifier,StrategyFeatures
from entry_timing.v2 import EntryTimingV2Engine
from entry_timing.v22 import EntryTimingV22ConfigService,IntradayEntryTriggerEngine,MarketRegimeV2Engine,PortfolioConcentrationGate,RegimeDeploymentGate,RegimeV2Result
from midday.asof import AsOfMarketDataResolver,cross_section_breadth,intraday_series_metrics
from midday.core import MiddayBaselineResolver,MiddayPoolResolver,SHANGHAI
from midday.llm_review import MiddayLLMReviewer
from midday.provider import MiddayIFindCollector
from midday.strategy_validation import MiddayStrategyValidator
from post_close.service import PostCloseActionService
from stock_codes import normalize_ts_code

class MiddayV22OneShotService:
    def __init__(self,session,*,output_root:Path):
        self.session=session;self.output_root=Path(output_root);self.app=get_app_config();self.mid_cfg=self.app.config_files["midday_recommendation"]["midday_recommendation"];self.v21=EntryTimingV2ConfigService(self.app).get();self.v22=EntryTimingV22ConfigService(self.app).get()

    def run(self,trade_date:date,cutoff:clock_time)->dict[str,Any]:
        started=time.perf_counter();cutoff_dt=datetime.combine(trade_date,cutoff,tzinfo=SHANGHAI);run=self._start_run(trade_date,cutoff_dt)
        try:
            baseline=MiddayBaselineResolver(self.session).resolve(trade_date,top_n=100);run.current_stage="PREFLIGHT"
            previous=self.session.scalar(select(MarketRegimeV2Snapshot).where(MarketRegimeV2Snapshot.trade_date<trade_date).order_by(MarketRegimeV2Snapshot.trade_date.desc(),MarketRegimeV2Snapshot.created_at.desc()))
            if previous is None:raise V22Failure("PREFLIGHT","PREVIOUS_REGIME_MISSING","Previous Market Regime V2 snapshot is required")
            if self.app.real_trading_enabled:raise V22Failure("PREFLIGHT","REAL_TRADING_ENABLED","ENABLE_REAL_TRADING must remain false")
            run.previous_regime=previous.current_state;self.session.commit()
            base_summary=EntryTimingV2ShadowService(self.session).run(baseline["trade_date"],quant_run_id=baseline["quant_run_id"],candidate_mode="QUANT_TOP100",force_shadow=True)
            base_run=self.session.scalar(select(AdmissionV2Run).where(AdmissionV2Run.run_id==base_summary["run_id"]));base_rows=list(self.session.scalars(select(EntryTimingV2Result).where(EntryTimingV2Result.run_id==base_run.run_id)))
            base_by={normalize_ts_code(row.stock_code):row for row in base_rows};v1_by={normalize_ts_code(row.stock_code):row for row in self.session.scalars(select(EntryTimingResult).where(EntryTimingResult.admission_run_id==base_run.v1_run_id))}
            hashes={"quant":base_run.quant_hash_before,"flash":base_run.flash_hash_before,"pro":base_run.pro_hash_before};run.quant_hash=hashes["quant"];run.flash_hash=hashes["flash"];run.pro_hash=hashes["pro"]
            truth=PostCloseActionService(self.session,self.app).position_truth(trade_date,now=cutoff_dt);pool=MiddayPoolResolver(self.session).resolve(trade_date,baseline,maximum=int(self.mid_cfg["pool"]["maximum_size"]),truth=truth)
            stock_master={normalize_ts_code(row.code):row for row in self.session.scalars(select(StockMaster))};candidate_items=[item for item in pool["items"] if set(item["sources"])&{"BASE_TOP100","ORDER_PLAN","MANUAL"}];codes=[item["stock_code"] for item in candidate_items]
            daily=TradeDateTimingCache(tushare_cache_root()).load(baseline["trade_date"],set(codes))["bars"]
            previous_closes={code:(float(daily[code][-1]["close"]) if daily.get(code) and daily[code][-1].get("close") is not None else None) for code in codes};index_codes=self._index_codes();collector=MiddayIFindCollector(self.session,self.app,self.mid_cfg["ifind"])
            if not collector.gate()["passed"]:raise V22Failure("PREFLIGHT","IFIND_AUTH_FAILED","Verified iFinD Shadow gate failed")
            collector._ensure_provider();resolver=AsOfMarketDataResolver(self.session,collector.provider,self.mid_cfg["ifind"])
            run.current_stage="DATA_RESOLUTION";self.session.commit()
            previous_index={row.index_code:float(row.close) for row in collector.provider.get_index_daily(index_codes,baseline["trade_date"],baseline["trade_date"])};index_snap,index_series=resolver.resolve(trade_date,cutoff,index_codes,previous_closes=previous_index,batch_size=len(index_codes));resolver.provider_calls+=1
            if not index_codes or any(index_snap[c].data_quality not in {"VALID_EXACT","VALID_NEAR_CUTOFF"} for c in index_codes):raise V22Failure("DATA_RESOLUTION","INDEX_ASOF_DATA_UNAVAILABLE","All configured indices require valid as-of minute reconstruction")
            stock_snap,stock_series=resolver.resolve(trade_date,cutoff,codes,previous_closes=previous_closes)
            valid_stocks={code:row for code,row in stock_snap.items() if row.data_quality in {"VALID_EXACT","VALID_NEAR_CUTOFF"}}
            model_codes={item["stock_code"] for item in candidate_items if "BASE_TOP100" in item["sources"]};breadth=cross_section_breadth({code:stock_snap[code] for code in model_codes if code in stock_snap})
            industries=self._industries(valid_stocks,stock_master);emotion=MarketEmotionEngine(self.v21).evaluate({"breadth":breadth,"limit_structure":{"limit_up_count":sum((r.change_pct_to_cutoff or 0)>=.095 for r in valid_stocks.values()),"limit_down_count":sum((r.change_pct_to_cutoff or 0)<=-.095 for r in valid_stocks.values())}})
            index_metrics={code:{**index_snap[code].to_dict(),**intraday_series_metrics(index_snap[code],index_series.get(code,[]),vwap_comparable=False),"vwap_to_cutoff":None} for code in index_codes}
            payload={"breadth":breadth,"limit_structure":{"limit_down_count":sum((r.change_pct_to_cutoff or 0)<=-.095 for r in valid_stocks.values())},"industries":industries,"indices":index_metrics,"market_emotion_score":emotion.market_emotion_score}
            history=self._regime_history(trade_date);regime=MarketRegimeV2Engine(self.v22).evaluate(payload,history)
            if previous.current_state=="CRASH" and regime.current_state not in {"CRASH","RISK_OFF","REPAIR"}:raise V22Failure("REGIME","REGIME_TRANSITION_VIOLATION",regime.current_state)
            run.current_stage="ADMISSION";run.midday_regime=regime.current_state;self.session.commit()
            assessed=self._assess(candidate_items,base_by,v1_by,stock_snap,stock_series,stock_master,industries,regime,emotion)
            strengths={row["sector_name"]:row["strength"] for row in industries};decisions=[]
            for pool_type in ("AI_POOL","MANUAL_CHALLENGE_POOL"):
                passed=[row for row in assessed if row.pool_type==pool_type and row.admission_status_v2=="PASS"]
                deployed=RegimeDeploymentGate(self.v22).apply(passed,regime.current_state,strengths);eligible=[row for row,d in deployed if d.status=="DEPLOYABLE"]
                concentrated=PortfolioConcentrationGate(self.v22).apply(eligible,strengths);crowd={id(row):d for row,d in concentrated}
                for row,deploy in deployed:decisions.append((row,deploy,crowd.get(id(row))))
            run.current_stage="TRIGGER";self.session.commit();results=[{**row.payload,"deployment_status":"NOT_EVALUATED","deployment_reason":"ADMISSION_NOT_PASS","position_multiplier":0,"crowding_status":"NOT_EVALUATED","crowding_reason":"ADMISSION_NOT_PASS","trigger_status":"NOT_ELIGIBLE","trigger_reasons":["ADMISSION_NOT_PASS"],"trigger_scores":{},"result_layer":"BLOCKED","afternoon_recheck":{"recheck_status":"NOT_ELIGIBLE","unmet_conditions":["ADMISSION_NOT_PASS"]}} for row in assessed if row.admission_status_v2!="PASS"]
            for row,deploy,crowd in decisions:
                eligible=deploy.status=="DEPLOYABLE" and crowd and crowd.status=="RETAINED";bars=self._trigger_bars(stock_series.get(row.stock_code,[]),stock_snap.get(row.stock_code),previous_closes.get(row.stock_code))
                sector_return=next((x["change_percent"] for x in industries if x["sector_name"]==row.industry),None)
                trigger=IntradayEntryTriggerEngine(self.v22).evaluate(strategy_id=row.strategy_id,regime=regime.current_state,bars=bars,sector_return=sector_return) if eligible else SimpleNamespace(status="NOT_ELIGIBLE",reasons=[deploy.reason if deploy.status!="DEPLOYABLE" else getattr(crowd,"reason","NOT_RETAINED")],scores={})
                status="MORNING_TRIGGERED" if trigger.status=="ENTRY_TRIGGERED" else trigger.status
                if deploy.status!="DEPLOYABLE":layer="REGIME_BLOCKED_HIGH_SCORE"
                elif crowd and crowd.status!="RETAINED":layer="CONCENTRATION_REVIEW"
                elif status=="MORNING_TRIGGERED":layer="BUY_READY"
                elif status=="WAITING_TRIGGER" and eligible:layer="AFTERNOON_WATCH"
                else:layer="BLOCKED"
                recheck=self._observation_plan(row,snap=stock_snap.get(row.stock_code),previous_close=previous_closes.get(row.stock_code),trigger=trigger,eligible=layer=="AFTERNOON_WATCH")
                results.append({**row.payload,"deployment_status":deploy.status,"deployment_reason":deploy.reason,"position_multiplier":min(.5,deploy.position_multiplier) if regime.current_state=="REPAIR" else deploy.position_multiplier,"crowding_status":getattr(crowd,"status","NOT_EVALUATED"),"crowding_reason":getattr(crowd,"reason","NOT_EVALUATED"),"trigger_status":status,"trigger_reasons":trigger.reasons,"trigger_scores":trigger.scores,"result_layer":layer,"afternoon_recheck":recheck})
            llm_status="COMPLETE";reviewer=MiddayLLMReviewer(self.session,self.mid_cfg);llm_candidates=[row for row in results if row["result_layer"] in {"BUY_READY","AFTERNOON_WATCH"}][:10]
            if llm_candidates:
                run.current_stage="LLM";self.session.commit()
                if not reviewer.gate()["passed"]:llm_status="RULE_ONLY_INCOMPLETE"
                else:
                    flash,failures=reviewer.review_many("FLASH",[(r["stock_code"],r) for r in llm_candidates],run.run_id);pro_input=[]
                    for r in llm_candidates:r["flash"]=flash.get(r["stock_code"]);pro_input.append((r["stock_code"],r)) if r.get("flash",{}).get("decision") in {"PASS","WATCH"} else None
                    pro,pro_failures=reviewer.review_many("PRO",pro_input[:3],run.run_id);failures+=pro_failures
                    for r in llm_candidates:r["pro"]=pro.get(r["stock_code"])
                    if failures:llm_status="LLM_STAGE_FAILED"
            run.current_stage="EXPORT";usage=reviewer.usage();counts={"quant_top100_count":100,"formal_pool_count":pool["counts"].get("ORDER_PLAN",0),"event_pool_count":0,"deduplicated_model_pool_count":len(model_codes),"manual_pool_count":pool["counts"].get("MANUAL",0),"index_requested":len(index_codes),"index_returned":sum(c in index_snap and index_snap[c].cutoff_compliant for c in index_codes),"stock_requested":len(codes),"stock_valid":len(valid_stocks),"minute_valid":len(valid_stocks),"admission":dict(Counter(r.admission_status_v2 for r in assessed)),"strategy":dict(Counter(r.strategy_id for r in assessed)),"strategy_source":dict(Counter(r.payload.get("strategy_source") for r in assessed)),"live_strategy_status":dict(Counter(r.payload.get("live_strategy_status") for r in assessed)),"unclassified_before":sum(r.payload.get("live_strategy_id")=="UNCLASSIFIED" for r in assessed),"unclassified_after":sum(r.strategy_id=="UNCLASSIFIED" for r in assessed),"deployment_pass":sum(r["deployment_status"]=="DEPLOYABLE" for r in results),"concentration_pass":sum(r["crowding_status"]=="RETAINED" for r in results),"trigger":dict(Counter(r["trigger_status"] for r in results)),"result_layers":dict(Counter(r["result_layer"] for r in results)),"final":sum(r["result_layer"]=="BUY_READY" for r in results),"watch":sum(r["result_layer"]=="AFTERNOON_WATCH" for r in results),"llm_calls":usage["calls"],"llm_tokens":usage["tokens"],"provider_calls":resolver.provider_calls,"cache_hits":resolver.cache_hits}
            regime_confidence,confidence_basis=_reported_regime_confidence(regime.input_coverage,breadth["scope"])
            report={"run_id":run.run_id,"trade_date":trade_date.isoformat(),"cutoff":cutoff_dt.isoformat(),"previous_regime":previous.current_state,"midday_regime":regime.current_state,"regime_confidence":regime_confidence,"regime_input_coverage":regime.input_coverage,"regime_confidence_basis":confidence_basis,"transition_reason":regime.state_reasons,"cooldown":regime.cooldown_remaining,"live_breadth_scope":breadth["scope"],"counts":counts,"provider_audit":resolver.audit,"excluded_post_cutoff_rows":resolver.excluded_post_cutoff_rows,"index_quality":index_metrics,"results":results,"llm_status":llm_status,"hashes":hashes,"real_orders":0,"virtual_orders":0,"scheduler":False}
            final_status="PARTIAL" if llm_status!="COMPLETE" else "EMPTY_POOL" if not counts["final"] else "SUCCESS";report["status"]=final_status;run.output_hash=_hash(report);run.input_hash=_hash([trade_date,cutoff_dt,baseline["quant_run_id"],pool["pool_hash"],resolver.audit]);run.counts_json=counts;run.provider_audit_json=resolver.audit;run.checkpoint_json={"breadth":breadth,"emotion":{"score":emotion.market_emotion_score,"state":emotion.emotion_state},"regime":regime.current_state,"regime_input_coverage":regime.input_coverage,"regime_confidence":regime_confidence,"regime_confidence_basis":report["regime_confidence_basis"]};run.status=final_status;run.current_stage="COMPLETED";run.completed_at=datetime.now(timezone.utc)
            for row in results:self.session.add(MiddayV22Result(run_id=run.run_id,stock_code=row["stock_code"],stock_name=row.get("stock_name"),pool_type=row["pool_type"],result_layer=row["result_layer"],payload_json=row))
            self.session.commit();paths=self._export(run,report);run.output_paths_json=paths;self.session.commit();return {**report,"output_paths":paths,"execution_ms":round((time.perf_counter()-started)*1000)}
        except Exception as exc:
            failure=exc if isinstance(exc,V22Failure) else V22Failure(run.current_stage,type(exc).__name__,str(exc));run.status="FAILED";run.failure_stage=failure.stage;run.error_code=failure.code;run.error_message=failure.message;run.completed_at=datetime.now(timezone.utc);run.real_orders=0;run.virtual_orders=0;run.scheduler_enabled=False;self.session.commit();return self._failed(run,started)

    def _start_run(self,trade_date,cutoff):
        run=MiddayV22Run(run_id=f"midday-v22-{uuid.uuid4().hex[:20]}",trade_date=trade_date,cutoff_time=cutoff,run_mode="LIVE_SHADOW_ONE_SHOT",status="RUNNING",current_stage="PREFLIGHT",input_hash=_hash([trade_date,cutoff,uuid.uuid4().hex]),config_hash=_hash(self.v22),counts_json={},provider_audit_json=[],checkpoint_json={},warnings_json=[],output_paths_json={},real_orders=0,virtual_orders=0,scheduler_enabled=False);self.session.add(run);self.session.commit();return run

    def _assess(self,items,base_by,v1_by,snapshots,series,masters,industries,regime,emotion):
        classifier=ShortTermStrategyClassifier(self.v21);validator=MiddayStrategyValidator(classifier);timing=EntryTimingV2Engine(self.v21);market_gate=StrategyMarketEmotionGate(self.v21);admission=StrategyAwareAdmissionEngine(self.v21);sector={x["sector_name"]:x for x in industries};output=[]
        for item in items:
            code=item["stock_code"];base=base_by.get(code);v1row=v1_by.get(code);snap=snapshots.get(code);master=masters.get(code);pools=[]
            if "BASE_TOP100" in item["sources"]:pools.append("AI_POOL")
            if "MANUAL" in item["sources"]:pools.append("MANUAL_CHALLENGE_POOL")
            if not pools:pools.append("AI_POOL")
            for pool_type in pools:
                if not base or not v1row or not snap or snap.data_quality not in {"VALID_EXACT","VALID_NEAR_CUTOFF"}:
                    output.append(SimpleNamespace(stock_code=code,stock_name=getattr(master,"name",None),pool_type=pool_type,industry=getattr(master,"industry",None),quant_rank=item["base_quant_rank"],strategy_id="UNCLASSIFIED",admission_status_v2="BLOCK",admission_ranking_score_v2=0,payload={"stock_code":code,"stock_name":getattr(master,"name",None),"pool_type":pool_type,"quant_rank":item["base_quant_rank"],"quant_score":item["base_quant_score"],"strategy_id":"UNCLASSIFIED","admission_status_v2":"BLOCK","block_reasons":["ASOF_DATA_OR_BASELINE_INSUFFICIENT"],"data_quality":getattr(snap,"data_quality","DATA_INSUFFICIENT")}));continue
                original=dict((base.diagnostics_json or {}).get("features") or {});industry=getattr(master,"industry",None) or "UNKNOWN";sector_row=sector.get(industry,{});bars=series.get(code,[]);volratio=_volume_ratio(bars)
                original.update({"close":snap.close_at_cutoff,"return_1d":snap.change_pct_to_cutoff,"volume_ratio":volratio,"pullback_volume_ratio":volratio,"sector_score":sector_row.get("strength",0)*100,"sector_breadth":sector_row.get("positive_ratio"),"stock_relative_strength":((snap.change_pct_to_cutoff or 0)-sector_row.get("return_decimal",0))*100,"market_regime":"PANIC_AND_REPAIR" if regime.current_state=="REPAIR" else regime.current_state,"market_emotion_state":emotion.emotion_state,"reversal_confirmation":bool(snap.vwap_to_cutoff and snap.close_at_cutoff>=snap.vwap_to_cutoff),"data_quality_score":100 if snap.data_quality=="VALID_EXACT" else 80})
                features=StrategyFeatures(**{k:original.get(k) for k in StrategyFeatures.__dataclass_fields__});validation,live_classification=validator.evaluate(base.strategy_id,float(base.strategy_fit_score),features);classification=SimpleNamespace(strategy_id=validation.strategy_id,strategy_fit_score=validation.live_strategy_fit,regime_compatibility_score=validation.live_regime_compatibility_score,classification_status=validation.classification_status);v1=EntryTimingAssessment(float(v1row.position_score),float(v1row.pullback_score),float(v1row.volume_price_score),float(v1row.sector_score),float(v1row.market_score),float(v1row.liquidity_score),float(v1row.entry_timing_score),float(v1row.data_quality_score),v1row.admission_status,list(v1row.risk_flags or []),dict(v1row.diagnostics or {}));v2=timing.evaluate(v1,classification)
                gate,reasons,inc=market_gate.evaluate(validation.strategy_id,emotion.emotion_state,market_regime=features.market_regime,strategy_fit=validation.live_strategy_fit,pullback_quality=v1.pullback_score/20*100,sector_score=v1.sector_score/15*100,reversal_confirmation=features.reversal_confirmation is True)
                decision=admission.decide(quant_score=float(base.quant_score),risk_score=float(base.risk_score) if base.risk_score is not None else None,timing_score=v2.entry_timing_v2_score,strategy_id=validation.strategy_id,strategy_fit=validation.live_strategy_fit,classification_status=validation.classification_status,market_gate_status=gate,market_gate_reasons=reasons,threshold_increment=inc,risk_flags=list(base.risk_flags_json or []),data_quality=features.data_quality_score)
                payload={"stock_code":code,"stock_name":getattr(master,"name",None) or base.stock_name,"pool_type":pool_type,"industry":industry,"quant_rank":base.quant_rank,"quant_score":float(base.quant_score),"strategy_id":validation.strategy_id,"base_strategy_id":validation.base_strategy_id,"strategy_source":validation.strategy_source,"live_strategy_id":validation.live_strategy_id,"live_strategy_status":validation.live_strategy_status,"live_strategy_fit_delta":validation.live_strategy_fit_delta,"strategy_still_valid":validation.strategy_still_valid,"strategy_fit_score":validation.live_strategy_fit,"entry_timing_v1_score":float(base.entry_timing_v1_score),"entry_timing_v2_1_score":v2.entry_timing_v2_score,"admission_score_v2_1":decision.admission_ranking_score,"admission_status_v2":decision.admission_status,"block_reasons":decision.block_reasons,"review_reasons":decision.review_reasons,"morning_return":snap.change_pct_to_cutoff,"morning_amplitude":snap.amplitude_to_cutoff,"price_vs_vwap":snap.close_at_cutoff/snap.vwap_to_cutoff-1 if snap.vwap_to_cutoff else None,"data_quality":snap.data_quality,"market_regime":regime.current_state,"market_emotion":emotion.emotion_state}
                output.append(SimpleNamespace(stock_code=code,stock_name=payload["stock_name"],pool_type=pool_type,industry=industry,quant_rank=base.quant_rank,strategy_id=validation.strategy_id,admission_status_v2=decision.admission_status,admission_ranking_score_v2=decision.admission_ranking_score,payload=payload))
        return output

    def _industries(self,snapshots,masters):
        groups=defaultdict(list)
        for code,row in snapshots.items():groups[getattr(masters.get(code),"industry",None) or "UNKNOWN"].append(row.change_pct_to_cutoff or 0)
        ranked=sorted(((k,sum(v)/len(v)) for k,v in groups.items()),key=lambda x:x[1],reverse=True);n=max(1,len(ranked)-1)
        return [{"sector_name":name,"change_percent":ret*100,"return_decimal":ret,"positive_ratio":sum((snapshots[c].change_pct_to_cutoff or 0)>0 for c in snapshots if (getattr(masters.get(c),"industry",None) or "UNKNOWN")==name)/len(vals),"strength":1-i/n} for i,(name,ret) in enumerate(ranked) for vals in [groups[name]]]
    def _regime_history(self,trade_date):
        rows=list(self.session.scalars(select(MarketRegimeV2Snapshot).where(MarketRegimeV2Snapshot.trade_date<trade_date).order_by(MarketRegimeV2Snapshot.trade_date.desc()).limit(3)));rows.reverse();return [RegimeV2Result(r.previous_state,r.current_state,r.raw_state,list(r.state_reasons_json),r.cooldown_remaining,r.confirmation_count,float(r.input_coverage)) for r in rows]
    def _index_codes(self):return [normalize_ts_code(x["index_code"]) for x in self.app.config_files["market_review"]["market_review"]["indices"] if x.get("index_code")]
    @staticmethod
    def _trigger_bars(bars,snap,prev):
        if not bars or not snap:return []
        value=dict(bars[-1]);value.update({"vwap":snap.vwap_to_cutoff,"volume_ratio":_volume_ratio(bars),"gap_percent":(bars[0]["open"]/prev-1)*100 if prev else None,"change_percent":(snap.change_pct_to_cutoff or 0)*100});return [value]*max(15,min(len(bars),16))
    def _export(self,run,report):
        from midday.v22_export import export_midday_v22
        return export_midday_v22(self.output_root,run,report)
    def _observation_plan(self,row,*,snap,previous_close,trigger,eligible):
        if not snap:return {"recheck_status":"DATA_INSUFFICIENT","unmet_conditions":["ASOF_SNAPSHOT_MISSING"]}
        trigger_cfg=self.v22["trigger"];max_vwap=snap.vwap_to_cutoff*(1+float(trigger_cfg["maximum_price_above_vwap_percent"])/100) if snap.vwap_to_cutoff else None
        max_chase=previous_close*(1+float(self.app.config_files["order_price"]["order_price"]["max_chase_percent"])) if previous_close else None
        maximum=min(x for x in (max_vwap,max_chase) if x is not None) if any(x is not None for x in (max_vwap,max_chase)) else None
        stop_pct=float(self.app.config_files["order_price"]["order_price"]["max_stop_loss_percent"]);stop=max(snap.session_low or 0,(snap.close_at_cutoff or 0)*(1-stop_pct)) or None
        return {"current_price":snap.close_at_cutoff,"afternoon_vwap":None,"morning_vwap_reference":snap.vwap_to_cutoff,"maximum_acceptable_price":maximum,"required_volume_ratio":float(trigger_cfg["minimum_volume_ratio"]),"required_sector_relative_strength":float(trigger_cfg["minimum_sector_relative_strength_percent"]),"invalidation_price":snap.vwap_to_cutoff,"stop_loss_reference":stop,"recheck_status":"PENDING" if eligible else "NOT_ELIGIBLE","unmet_conditions":list(getattr(trigger,"reasons",[]) or [])}
    def _failed(self,run,started):
        report={"run_id":run.run_id,"trade_date":run.trade_date.isoformat(),"cutoff":run.cutoff_time.isoformat(),"status":"FAILED","failure_stage":run.failure_stage,"error_code":run.error_code,"error_message":run.error_message,"real_orders":0,"virtual_orders":0,"scheduler":False};paths=self._export(run,report);run.output_paths_json=paths;self.session.commit();return {**report,"output_paths":paths,"execution_ms":round((time.perf_counter()-started)*1000)}

class V22Failure(RuntimeError):
    def __init__(self,stage,code,message):super().__init__(message);self.stage=stage;self.code=code;self.message=message
def _feature(row,key):return ((row.diagnostics_json or {}).get("features") or {}).get(key) if row else None
def _volume_ratio(bars):
    values=[float(x.get("volume") or 0) for x in bars];mid=max(1,len(values)//2);a=sum(values[:mid])/mid if values[:mid] else 0;b=sum(values[mid:])/max(1,len(values[mid:]));return b/a if a else None
def _hash(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,default=str,separators=(",",":")).encode()).hexdigest()
def _reported_regime_confidence(input_coverage,scope):
    factor=.75 if scope=="QUANT_TOP100_CROSS_SECTION" else 1.0
    return round(float(input_coverage)*factor,4),"LIMITED_QUANT_TOP100_CROSS_SECTION" if factor<1 else "FULL_SCOPE"
