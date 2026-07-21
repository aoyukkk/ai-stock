from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time
from typing import Any

from midday.asof import AsOfMarketDataResolver
from midday.core import SHANGHAI
from stock_codes import normalize_ts_code


class FullADataCapacityError(RuntimeError):
    pass


class FullADataFreshnessError(RuntimeError):
    pass


@dataclass
class FullAAsOfResult:
    snapshots: dict[str, dict[str, Any]]
    minute_series: dict[str, list[dict[str, Any]]]
    method: str
    validation: dict[str, Any]
    audit: list[dict[str, Any]]
    requested: int
    returned: int
    coverage: float
    provider_calls: int
    hf_batch_count: int
    excluded_post_cutoff_rows: int


class FullAAsOfResolver:
    def __init__(self, provider, config: dict[str, Any], *, now=None) -> None:
        self.provider=provider;self.config=config;self.now=now or (lambda:datetime.now(SHANGHAI));self.audit=[];self.hf_batches=0

    def resolve(self,trade_date:date,cutoff:clock_time,codes:list[str],*,previous_closes:dict[str,float|None]) -> FullAAsOfResult:
        normalized=list(dict.fromkeys(normalize_ts_code(code) for code in codes));asof=f"{trade_date.isoformat()} {cutoff.isoformat()}";snapshots={};series={}
        snapshot_size=max(1,int(self.config["snapshot_batch_size"]));minute_size=max(1,int(self.config["minute_reconstruction_batch_size"]));maximum=int(self.config["maximum_provider_calls"])
        snapshot_plan=math.ceil(len(normalized)/snapshot_size)+(1 if normalized else 0)
        minute_plan=math.ceil(len(normalized)/minute_size)
        if snapshot_plan>maximum and minute_plan>maximum:
            raise FullADataCapacityError(
                "FULL_A_DATA_CAPACITY_FAILED:"
                f"eligible={len(normalized)}:snapshot_batch_size={snapshot_size}:snapshot_calls={snapshot_plan}:"
                f"minute_batch_size={minute_size}:minute_calls={minute_plan}:maximum={maximum}"
            )
        for batch_id,offset in enumerate(range(0,len(normalized),snapshot_size),1):
            batch=normalized[offset:offset+snapshot_size];started=time.perf_counter();rows=self.provider.get_historical_snapshots(batch,asof);by={normalize_ts_code(row.stock_code):row for row in rows}
            self.audit.append(self._audit("HISTORICAL_SNAPSHOT",batch_id,batch,len(rows),started,None))
            for code,row in by.items():snapshots[code]=self._snapshot_dict(code,row,previous_closes.get(code),trade_date,cutoff)
        missing=[code for code in normalized if code not in snapshots]
        validation_count=min(int(self.config["validation_sample_count"]),len(snapshots));estimated=len(self.audit)+math.ceil(len(missing)/minute_size)+(1 if validation_count else 0)
        if estimated>maximum:
            raise FullADataCapacityError(f"FULL_A_DATA_CAPACITY_FAILED:estimated_calls={estimated}:maximum={maximum}:snapshot_returned={len(snapshots)}:minute_missing={len(missing)}")
        validation=self._validate_snapshot_semantics(trade_date,cutoff,self._stratified_sample(sorted(snapshots),validation_count),snapshots,previous_closes)
        if validation_count and not validation["passed"]:
            # Snapshot semantics are unsafe; reconstruct the whole universe only when capacity permits.
            missing=normalized;snapshots={};estimated=len(self.audit)+math.ceil(len(missing)/minute_size)+1
            if estimated>maximum:raise FullADataCapacityError(f"FULL_A_DATA_CAPACITY_FAILED:snapshot_validation_failed:estimated_calls={estimated}:maximum={maximum}")
        helper=AsOfMarketDataResolver(None,self.provider,{"volume_semantics":"INCREMENTAL_SHARES","amount_semantics":"INCREMENTAL_CNY","near_cutoff_tolerance_seconds":180})
        start=f"{trade_date.isoformat()} 09:30:00"
        for batch_id,offset in enumerate(range(0,len(missing),minute_size),1):
            batch=missing[offset:offset+minute_size];started=time.perf_counter();values=self.provider.get_minute_bars_batch(batch,start,asof,"1m");self.hf_batches+=1
            self.audit.append(self._audit("HIGH_FREQUENCY_RECONSTRUCTION",batch_id,batch,sum(len(value) for value in values.values()),started,None))
            requested_at=self.now();received_at=self.now()
            for code in batch:
                snap,bars=helper.reconstruct(code,values.get(code,[]),trade_date,cutoff,previous_closes.get(code),requested_at,received_at)
                item=snap.to_dict();item["data_quality"]="VALID_RECONSTRUCTED" if item["data_quality"] in {"VALID_EXACT","VALID_NEAR_CUTOFF"} else item["data_quality"]
                snapshots[code]=item;series[code]=bars
        valid={code:row for code,row in snapshots.items() if row.get("data_quality") in {"VALID_EXACT","VALID_RECONSTRUCTED"} and row.get("cutoff_compliant")}
        coverage=len(valid)/len(normalized) if normalized else 0
        minimum=float(self.config["minimum_full_a_coverage"])
        if coverage<minimum:raise FullADataFreshnessError(f"FULL_A_DATA_FRESHNESS_FAILED:coverage={coverage:.6f}:minimum={minimum:.6f}")
        methods={row["reconstruction_method"] for row in valid.values()};method="LUNCH_BREAK_FROZEN_SNAPSHOT_VALIDATED" if methods=={"IFIND_HISTORICAL_SNAPSHOT"} else "HYBRID_HISTORICAL_SNAPSHOT_AND_1M_RECONSTRUCTION"
        return FullAAsOfResult(valid,series,method,validation,self.audit,len(normalized),len(valid),coverage,self.provider.client.call_count,self.hf_batches,helper.excluded_post_cutoff_rows)

    def fetch_minutes(self,trade_date:date,cutoff:clock_time,codes:list[str],*,maximum_additional_calls:int)->tuple[dict[str,list[dict[str,Any]]],list[dict[str,Any]]]:
        size=max(1,int(self.config["minute_reconstruction_batch_size"]));needed=math.ceil(len(codes)/size)
        if needed>maximum_additional_calls:raise FullADataCapacityError("FULL_A_TRIGGER_MINUTE_CAPACITY_FAILED")
        output={};asof=f"{trade_date.isoformat()} {cutoff.isoformat()}";start=f"{trade_date.isoformat()} 09:30:00"
        for batch_id,offset in enumerate(range(0,len(codes),size),1):
            batch=codes[offset:offset+size];started=time.perf_counter();values=self.provider.get_minute_bars_batch(batch,start,asof,"1m");self.hf_batches+=1
            self.audit.append(self._audit("TRIGGER_MINUTE_REUSE",batch_id,batch,sum(len(value) for value in values.values()),started,None))
            for code,rows in values.items():
                output[code]=[{"bar_time":str(row.datetime),"open":row.open,"high":row.high,"low":row.low,"close":row.close,"volume":row.volume,"amount":row.amount} for row in rows]
        return output,self.audit

    def _validate_snapshot_semantics(self,trade_date,cutoff,codes,snapshots,previous_closes):
        if not codes:return {"passed":False,"sample_count":0,"reason":"NO_SNAPSHOT_SAMPLE"}
        started=time.perf_counter();values=self.provider.get_minute_bars_batch(codes,f"{trade_date.isoformat()} 09:30:00",f"{trade_date.isoformat()} {cutoff.isoformat()}","1m");self.hf_batches+=1
        self.audit.append(self._audit("SNAPSHOT_SEMANTIC_VALIDATION",1,codes,sum(len(value) for value in values.values()),started,None))
        helper=AsOfMarketDataResolver(None,self.provider,{"volume_semantics":"INCREMENTAL_SHARES","amount_semantics":"INCREMENTAL_CNY","near_cutoff_tolerance_seconds":180})
        checks=[]
        for code in codes:
            rebuilt,_=helper.reconstruct(code,values.get(code,[]),trade_date,cutoff,previous_closes.get(code),self.now(),self.now());snap=snapshots[code]
            price_ok=all(_close(snap.get(left),getattr(rebuilt,right),.002) for left,right in (("session_open","session_open"),("session_high","session_high"),("session_low","session_low"),("close_at_cutoff","close_at_cutoff")))
            volume_ok=_close(snap.get("volume_to_cutoff"),rebuilt.volume_to_cutoff,.08);amount_ok=_close(snap.get("amount_to_cutoff"),rebuilt.amount_to_cutoff,.08)
            checks.append({"stock_code":code,"price_consistent":price_ok,"volume_consistent":volume_ok,"amount_consistent":amount_ok,"passed":price_ok and volume_ok and amount_ok})
        passed=sum(item["passed"] for item in checks);required=max(1,math.ceil(len(checks)*.9))
        return {"passed":passed>=required,"sample_count":len(checks),"consistent_count":passed,"required_count":required,"price_tolerance_relative":.002,"volume_amount_tolerance_relative":.08,"checks":checks}

    @staticmethod
    def _snapshot_dict(code,row,previous_close,trade_date,cutoff):
        latest=float(row.latest);opening=float(row.open);high=float(row.high);low=float(row.low);pre=float(row.pre_close or previous_close or 0);volume=float(row.volume);amount=float(row.amount)
        cutoff_dt=datetime.combine(trade_date,cutoff,tzinfo=SHANGHAI)
        return {"code":code,"session_open":opening,"session_high":high,"session_low":low,"close_at_cutoff":latest,"volume_to_cutoff":volume,"amount_to_cutoff":amount,"vwap_to_cutoff":amount/volume if volume else None,"change_pct_to_cutoff":latest/pre-1 if pre else None,"amplitude_to_cutoff":(high-low)/pre if pre else None,"last_eligible_bar_time":cutoff_dt.isoformat(),"minute_bar_count":0,"missing_bar_count":0,"data_quality":"VALID_EXACT","reconstruction_method":"IFIND_HISTORICAL_SNAPSHOT","cutoff_compliant":str(row.datetime).startswith(f"{trade_date.isoformat()} {cutoff.isoformat()}"),"provenance":{"provider":"IFIND_HTTP","provider_payload_time":row.datetime,"exchange_event_time":row.datetime,"cutoff_time":cutoff_dt.isoformat(),"time_semantics":"EXPLICIT_HISTORICAL_SNAPSHOT","cutoff_compliant":True}}

    def _audit(self,capability,batch_id,codes,returned,started,error):
        return {"capability":capability,"batch_id":batch_id,"requested_code_count":len(codes),"returned_rows":returned,"latency_ms":round((time.perf_counter()-started)*1000),"error_category":error,"requested_at":self.now().isoformat(),"concurrency":1}

    @staticmethod
    def _stratified_sample(codes,count):
        if count<=0:return []
        if count>=len(codes):return list(codes)
        return [codes[round(index*(len(codes)-1)/(count-1))] for index in range(count)] if count>1 else [codes[0]]


def _close(left:Any,right:Any,tolerance:float)->bool:
    if left is None or right is None:return False
    left=float(left);right=float(right);return abs(left-right)<=max(1e-8,max(abs(left),abs(right))*tolerance)
