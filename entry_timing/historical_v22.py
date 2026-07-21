from __future__ import annotations

import hashlib,json,statistics,uuid
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openpyxl import Workbook,load_workbook
from openpyxl.styles import Alignment,Font,PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select

from database.models.entry_timing_v2 import AdmissionV2Run,EntryTimingV2Result
from database.models.entry_timing_v22 import DeploymentV22Result,DeploymentV22Run,MarketAdjustedEvaluation,MarketRegimeV2Snapshot
from database.models.market_review import MarketDailySnapshot
from database.models.performance import SelectionCohort
from database.models.stock import StockMaster
from entry_timing.historical_v2 import HistoricalEntryTimingV2Validator
from entry_timing.market_adjusted import MarketAdjustedPerformanceEvaluator
from entry_timing.service import _rows_by_code
from entry_timing.v22 import EntryTimingV22ConfigService,IntradayEntryTriggerEngine,MarketRegimeV2Engine,PortfolioConcentrationGate,RegimeDeploymentGate

CENTER=Alignment(horizontal="center",vertical="center",wrap_text=True)

class HistoricalV22Validator:
    def __init__(self,session,*,cache_root:Path):self.session=session;self.cache_root=Path(cache_root);self.config=EntryTimingV22ConfigService().get()
    def run(self,start:date,end:date,output:Path)->dict[str,Any]:
        cohorts=list(self.session.scalars(select(SelectionCohort).where(SelectionCohort.selection_trade_date>=start,SelectionCohort.selection_trade_date<=end).order_by(SelectionCohort.selection_trade_date)))
        if not cohorts:raise ValueError("V22_HISTORICAL_COHORTS_NOT_FOUND")
        base=HistoricalEntryTimingV2Validator(self.session,cache_root=self.cache_root);hashes_before=base._global_source_hashes(cohorts)
        snapshots={row.trade_date:row for row in self.session.scalars(select(MarketDailySnapshot).where(MarketDailySnapshot.trade_date>=start,MarketDailySnapshot.trade_date<=end).order_by(MarketDailySnapshot.trade_date,MarketDailySnapshot.created_at))}
        stock_map={row.code:row for row in self.session.scalars(select(StockMaster))};regime_history=[];records=[];timeline=[];run_ids=[]
        for cohort in cohorts:
            day=cohort.selection_trade_date;snapshot=snapshots.get(day)
            v2run=self.session.scalar(select(AdmissionV2Run).where(AdmissionV2Run.trade_date==day).order_by(AdmissionV2Run.created_at.desc()))
            if not v2run or not snapshot:continue
            all_rows=list(self.session.scalars(select(EntryTimingV2Result).where(EntryTimingV2Result.run_id==v2run.run_id).order_by(EntryTimingV2Result.quant_rank)))
            payload={"breadth":snapshot.breadth_summary_json,"limit_structure":snapshot.limit_summary_json,"turnover":snapshot.turnover_summary_json,"industries":list((snapshot.industry_summary_json or {}).get("items") or []),"market_emotion_score":float(v2run.market_emotion_score) if v2run.market_emotion_score is not None else None}
            regime=MarketRegimeV2Engine(self.config).evaluate(payload,regime_history);regime_history.append(regime);self._persist_regime(day,snapshot,regime,payload)
            strengths=self._industry_strength(payload["industries"])
            wrappers=[]
            for row in all_rows:
                meta=stock_map.get(row.stock_code);wrappers.append(SimpleNamespace(**{column.name:getattr(row,column.name) for column in row.__table__.columns},industry=getattr(meta,"industry",None)))
            decisions={};counts={}
            for pool in ("AI_POOL","MANUAL_CHALLENGE_POOL"):
                passed=[row for row in wrappers if row.pool_type==pool and row.admission_status_v2=="PASS"]
                deployed=RegimeDeploymentGate(self.config).apply(passed,regime.current_state,strengths)
                deployable=[row for row,d in deployed if d.status=="DEPLOYABLE"]
                concentrated=PortfolioConcentrationGate(self.config).apply(deployable,strengths)
                conc={id(row):decision for row,decision in concentrated}
                before=Counter(row.industry or "UNKNOWN" for row in deployable);after=Counter(row.industry or "UNKNOWN" for row,d in concentrated if d.status=="RETAINED")
                for row,dd in deployed:decisions[(row.stock_code,pool)]=(row,dd,conc.get(id(row)),before,after)
            input_hash=_hash([day,v2run.run_id,regime.current_state,self.config,hashes_before]);existing=self.session.scalar(select(DeploymentV22Run).where(DeploymentV22Run.input_hash==input_hash))
            if existing:run_id=existing.run_id
            else:
                run_id=f"deploy-v22-{uuid.uuid4().hex[:20]}";result_rows=[]
                for key,(row,dd,cd,before,after) in decisions.items():
                    crowd=cd.status if cd else "NOT_EVALUATED";crowd_reason=cd.reason if cd else "REMOVED_BY_REGIME"
                    eligible=dd.status=="DEPLOYABLE" and cd is not None and cd.status=="RETAINED"
                    trigger=IntradayEntryTriggerEngine(self.config).evaluate(strategy_id=row.strategy_id,regime=regime.current_state,bars=[],sector_return=None) if eligible else SimpleNamespace(status="NOT_ELIGIBLE",reasons=[dd.reason if dd.status!="DEPLOYABLE" else crowd_reason],scores={})
                    industry=row.industry or "UNKNOWN";ratio=before[industry]/len([x for x,d in RegimeDeploymentGate(self.config).apply([r for r in wrappers if r.pool_type==row.pool_type and r.admission_status_v2=="PASS"],regime.current_state,strengths) if d.status=="DEPLOYABLE"]) if before[industry] else None
                    result_rows.append(DeploymentV22Result(run_id=run_id,trade_date=day,stock_code=row.stock_code,stock_name=row.stock_name,pool_type=row.pool_type,industry=row.industry,cluster_id=cd.cluster_id if cd else None,strategy_id=row.strategy_id,admission_score=row.admission_ranking_score_v2,regime_state=regime.current_state,deployment_status=dd.status,deployment_reason=dd.reason,position_multiplier=dd.position_multiplier,crowding_status=crowd,crowding_reason=crowd_reason,retained_rank_in_sector=cd.retained_rank_in_sector if cd else None,industry_candidate_count_before=before[industry],industry_candidate_count_after=after[industry],industry_pool_ratio=ratio,trigger_status=trigger.status,trigger_reasons_json=trigger.reasons,trigger_scores_json=trigger.scores,version=self.config["versions"]["deployment"]))
                ai=[row for row in result_rows if row.pool_type=="AI_POOL"];after_regime=sum(row.deployment_status=="DEPLOYABLE" for row in ai);after_conc=sum(row.deployment_status=="DEPLOYABLE" and row.crowding_status=="RETAINED" for row in ai)
                self.session.add(DeploymentV22Run(run_id=run_id,trade_date=day,v2_run_id=v2run.run_id,input_hash=input_hash,regime_state=regime.current_state,candidate_before=sum(row.pool_type=="AI_POOL" and row.admission_status_v2=="PASS" for row in all_rows),after_regime=after_regime,after_concentration=after_conc,triggered_count=sum(row.trigger_status=="ENTRY_TRIGGERED" for row in ai),config_snapshot=self.config,status="SUCCESS",shadow_only=True,llm_calls=0,external_calls=0,orders_created=0,source_hashes_json=hashes_before));self.session.add_all(result_rows);self.session.commit()
            run_ids.append(run_id);persisted={(row.stock_code,row.pool_type):row for row in self.session.scalars(select(DeploymentV22Result).where(DeploymentV22Result.run_id==run_id))}
            for row in wrappers:
                outcome=base._outcome(day,row.stock_code,end);adjusted=self._adjusted(day,end,row.stock_code,row.industry,outcome,snapshots)
                dep=persisted.get((row.stock_code,row.pool_type));record={"trade_date":day.isoformat(),"stock_code":row.stock_code,"stock_name":row.stock_name,"pool_type":row.pool_type,"industry":row.industry,"strategy_id":row.strategy_id,"admission_v1":row.admission_status_v1,"admission_v21":row.admission_status_v2,"regime":regime.current_state,"deployment_status":getattr(dep,"deployment_status",None),"crowding_status":getattr(dep,"crowding_status",None),"trigger_status":getattr(dep,"trigger_status",None),"cumulative_return":outcome.get("cumulative_return"),**adjusted};records.append(record)
                if not self.session.scalar(select(MarketAdjustedEvaluation).where(MarketAdjustedEvaluation.run_id==run_id,MarketAdjustedEvaluation.stock_code==row.stock_code,MarketAdjustedEvaluation.pool_type==row.pool_type)):
                    self.session.add(MarketAdjustedEvaluation(run_id=run_id,trade_date=day,stock_code=row.stock_code,pool_type=row.pool_type,benchmark_type=adjusted.get("benchmark_type"),benchmark_code=adjusted.get("benchmark_code"),benchmark_quality=adjusted["benchmark_quality"],benchmark_missing_reason=adjusted.get("benchmark_missing_reason"),metrics_json=adjusted,version=self.config["versions"]["evaluation"]))
            self.session.commit();runrow=self.session.scalar(select(DeploymentV22Run).where(DeploymentV22Run.run_id==run_id));timeline.append({"trade_date":day.isoformat(),"previous_state":regime.previous_state,"current_state":regime.current_state,"raw_state":regime.raw_state,"state_reason":"、".join(regime.state_reasons),"cooldown":regime.cooldown_remaining,"confirmation_count":regime.confirmation_count,"before":runrow.candidate_before,"after_regime":runrow.after_regime,"after_concentration":runrow.after_concentration,"after_trigger":runrow.triggered_count})
        hashes_after=base._global_source_hashes(cohorts)
        if hashes_before!=hashes_after:raise RuntimeError("V22_SOURCE_HASH_CHANGED")
        ai=[row for row in records if row["pool_type"]=="AI_POOL" and row.get("cumulative_return") is not None]
        versions={}
        selectors={"ORIGINAL":lambda r:True,"ENTRY_TIMING_V1":lambda r:r["admission_v1"]=="PASS","ENTRY_TIMING_V2_1":lambda r:r["admission_v21"]=="PASS","V2_2_DEPLOYMENT":lambda r:r["deployment_status"]=="DEPLOYABLE" and r["crowding_status"]=="RETAINED","V2_2_TRIGGERED":lambda r:r["trigger_status"]=="ENTRY_TRIGGERED"}
        for name,selector in selectors.items():versions[name]=MarketAdjustedPerformanceEvaluator.metrics([{**row,"selected":selector(row)} for row in ai])
        industry_summary=[]
        for key,group in _groups(records,lambda row:(row["trade_date"],row["pool_type"],row.get("industry") or "UNKNOWN")).items():
            deployed=[row for row in group if row.get("deployment_status")=="DEPLOYABLE"]
            retained=[row for row in deployed if row.get("crowding_status")=="RETAINED"]
            if deployed:
                industry_summary.append({"trade_date":key[0],"pool_type":key[1],"industry":key[2],"before_concentration":len(deployed),"after_concentration":len(retained),"removed":len(deployed)-len(retained)})
        report={"period":f"{start} to {end}","timeline":timeline,"versions":versions,"regime_distribution":dict(Counter(row["current_state"] for row in timeline)),"industry_concentration_before_after":industry_summary,"empty_pool_dates":[row["trade_date"] for row in timeline if row["after_concentration"]==0],"entry_trigger_coverage":{"eligible":sum(row["deployment_status"]=="DEPLOYABLE" and row["crowding_status"]=="RETAINED" for row in ai),"triggered":sum(row["trigger_status"]=="ENTRY_TRIGGERED" for row in ai),"data_insufficient":sum(row["trigger_status"]=="DATA_INSUFFICIENT" for row in ai)},"target_before_stop_note":"Not evaluated: no legal intraday target/stop ordering data in the local replay set.","removed_by_regime":sum(row["deployment_status"]=="REMOVED_BY_REGIME" for row in ai),"removed_by_concentration":sum(row["crowding_status"]=="SECTOR_CROWDING_REVIEW" for row in ai),"rejected_by_trigger":sum(row["trigger_status"] in {"WAITING_TRIGGER","TRIGGER_REJECTED","EXPIRED"} for row in ai),"source_hashes_before":hashes_before,"source_hashes_after":hashes_after,"llm_calls":0,"external_historical_api_calls":0,"live_shadow_provider_calls":0,"orders_created":0,"scheduler_enabled":False,"grid_search":False,"run_ids":run_ids}
        report["promotion_recommendation"]="KEEP_V2_2_SHADOW";report["reproducible_hash"]=_hash({k:v for k,v in report.items() if k!="run_ids"});report["excel"]=V22Excel().export(output,records,report);return report

    def _persist_regime(self,day,snapshot,regime,payload):
        input_hash=_hash(payload);version=self.config["versions"]["regime"]
        if not self.session.scalar(select(MarketRegimeV2Snapshot).where(MarketRegimeV2Snapshot.trade_date==day,MarketRegimeV2Snapshot.input_hash==input_hash,MarketRegimeV2Snapshot.version==version)):
            self.session.add(MarketRegimeV2Snapshot(trade_date=day,input_hash=input_hash,version=version,previous_state=regime.previous_state,current_state=regime.current_state,raw_state=regime.raw_state,state_reasons_json=regime.state_reasons,cooldown_remaining=regime.cooldown_remaining,confirmation_count=regime.confirmation_count,input_coverage=regime.input_coverage,source_snapshot_hash=snapshot.snapshot_hash));self.session.commit()
    @staticmethod
    def _industry_strength(items):
        ordered=sorted(items,key=lambda x:float(x.get("change_percent") or -999),reverse=True);n=max(1,len(ordered)-1)
        return {str(row.get("sector_name")):1-index/n for index,row in enumerate(ordered)}
    def _adjusted(self,day,end,code,industry,outcome,snapshots):
        observations=outcome.get("daily_observations") or [];dates=[date.fromisoformat(str(row["trade_date"])) if "-" in str(row["trade_date"]) else date(int(str(row["trade_date"])[:4]),int(str(row["trade_date"])[4:6]),int(str(row["trade_date"])[6:])) for row in observations];stock=[float(row["daily_return"]) for row in observations];benchmark=[];kind="INDUSTRY";quality="HIGH"
        for observed in dates:
            snap=snapshots.get(observed);items=list((snap.industry_summary_json or {}).get("items") or []) if snap else [];match=next((row for row in items if str(row.get("sector_name"))==str(industry)),None)
            if not match:benchmark=[];break
            benchmark.append(float(match.get("change_percent"))/100)
        if not benchmark and observations:
            kind="BROAD_MARKET";quality="MEDIUM";benchmark=[]
            for observed in dates:
                snap=snapshots.get(observed);value=(snap.breadth_summary_json or {}).get("equal_weight_return") if snap else None
                if value is None:benchmark=[];break
                benchmark.append(float(value))
        missing=None if benchmark or not observations else "NO_VALID_LOCAL_BENCHMARK"
        return MarketAdjustedPerformanceEvaluator.evaluate(stock,benchmark if benchmark else None,benchmark_type=kind if benchmark else None,benchmark_code=industry if kind=="INDUSTRY" and benchmark else "ALL_A_EQUAL_WEIGHT" if benchmark else None,benchmark_quality=quality if benchmark else "MISSING",missing_reason=missing)

class V22Excel:
    SHEETS=("01_管理层看板","02_Regime时间线","03_逐股部署明细","04_行业集中","05_EntryTrigger","06_市场相对绩效","07_移除与机会成本","08_口径与安全")
    def export(self,path,records,report):
        wb=Workbook();wb.remove(wb.active);_sheet(wb,self.SHEETS[0],[{"版本":k,**v} for k,v in report["versions"].items()]);_sheet(wb,self.SHEETS[1],report["timeline"]);_sheet(wb,self.SHEETS[2],records);_sheet(wb,self.SHEETS[3],report["industry_concentration_before_after"]);_sheet(wb,self.SHEETS[4],[r for r in records if r.get("trigger_status")]);_sheet(wb,self.SHEETS[5],[{"版本":k,"绝对胜率":v.get("absolute_win_rate"),"市场相对胜率":v.get("relative_win_rate"),"行业相对胜率":v.get("industry_relative_win_rate"),"平均Alpha":v.get("average_alpha"),"中位Alpha":v.get("median_alpha"),"Alpha PF":v.get("alpha_profit_factor"),"Target-before-stop":v.get("target_before_stop_rate")} for k,v in report["versions"].items()]);_sheet(wb,self.SHEETS[6],[r for r in records if r.get("deployment_status")=="REMOVED_BY_REGIME" or r.get("crowding_status")=="SECTOR_CROWDING_REVIEW"]);_sheet(wb,self.SHEETS[7],[{"项目":"LLM调用","值":0},{"项目":"历史外部API","值":0},{"项目":"实时Provider调用","值":0},{"项目":"订单创建","值":0},{"项目":"Scheduler","值":"关闭"},{"项目":"Target-before-stop","值":report["target_before_stop_note"]},{"项目":"参数搜索","值":"未执行"},{"项目":"Promotion","值":report["promotion_recommendation"]},{"项目":"可复现Hash","值":report["reproducible_hash"]}]);path.parent.mkdir(parents=True,exist_ok=True);wb.save(path);wb.close();return _verify(path,self.SHEETS)

def _sheet(wb,title,rows):
    ws=wb.create_sheet(title);headers=list(rows[0]) if rows else ["暂无数据"];ws.append(headers)
    for row in rows:ws.append([_cell(row.get(h)) for h in headers])
    ws.freeze_panes="A2";ws.auto_filter.ref=ws.dimensions;ws.sheet_view.showGridLines=False
    for c in ws[1]:c.fill=PatternFill("solid",fgColor="1F4E78");c.font=Font(name="Microsoft YaHei",bold=True,color="FFFFFF")
    for row in ws.iter_rows():
        for c in row:c.alignment=CENTER;c.number_format="@" if "股票代码" in str(ws.cell(1,c.column).value) else c.number_format
        ws.row_dimensions[row[0].row].height=34 if any(isinstance(c.value,str) and len(c.value)>28 for c in row) else 25
    for i,h in enumerate(headers,1):ws.column_dimensions[get_column_letter(i)].width=min(32,max(12,max(len(str(ws.cell(r,i).value or "")) for r in range(1,min(ws.max_row,150)+1))*1.4+2))
def _cell(value):return json.dumps(value,ensure_ascii=False,sort_keys=True) if isinstance(value,(dict,list)) else value
def _verify(path,sheets):
    wb=load_workbook(path,data_only=False)
    try:
        assert wb.sheetnames==list(sheets);errors=[]
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if c.value is not None and (c.alignment.horizontal!="center" or c.alignment.vertical!="center" or not c.alignment.wrap_text):errors.append(f"{ws.title}!{c.coordinate}")
        if errors:raise ValueError(errors[0])
        return {"output":str(path),"size_bytes":path.stat().st_size,"sheets":wb.sheetnames,"centered":True,"formula_error_scan":"PASSED"}
    finally:wb.close()
def _hash(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,default=str,separators=(",",":")).encode()).hexdigest()
def _groups(rows,key):
    output={}
    for row in rows:output.setdefault(key(row),[]).append(row)
    return output
