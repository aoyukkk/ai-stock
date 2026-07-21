from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, time as clock_time, timedelta
from statistics import median
from statistics import pstdev
from typing import Any

from sqlalchemy import select

from database.models import MarketSnapshotShadow
from midday.core import SHANGHAI
from stock_codes import normalize_ts_code


@dataclass(frozen=True)
class AsOfSnapshot:
    code: str
    session_open: float | None
    session_high: float | None
    session_low: float | None
    close_at_cutoff: float | None
    volume_to_cutoff: float | None
    amount_to_cutoff: float | None
    vwap_to_cutoff: float | None
    change_pct_to_cutoff: float | None
    amplitude_to_cutoff: float | None
    last_eligible_bar_time: str | None
    minute_bar_count: int
    missing_bar_count: int
    data_quality: str
    reconstruction_method: str
    cutoff_compliant: bool
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AsOfMarketDataResolver:
    def __init__(self, session, provider, config: dict[str, Any], *, now=None) -> None:
        self.session = session
        self.provider = provider
        self.config = config
        self.now = now or (lambda: datetime.now(SHANGHAI))
        self.audit: list[dict[str, Any]] = []
        self.provider_calls = 0
        self.cache_hits = 0
        self.excluded_post_cutoff_rows = 0

    def resolve(
        self, trade_date: date, cutoff: clock_time, codes: list[str], *, previous_closes: dict[str, float | None], batch_size: int | None = None,
    ) -> tuple[dict[str, AsOfSnapshot], dict[str, list[dict[str, Any]]]]:
        normalized = list(dict.fromkeys(normalize_ts_code(code) for code in codes))
        output: dict[str, AsOfSnapshot] = {}
        series: dict[str, list[dict[str, Any]]] = {}
        pending=[]
        for code in normalized:
            exact=self._exact_snapshot(trade_date,cutoff,code,previous_closes.get(code)) if bool(self.config.get("allow_persisted_snapshot",True)) else None
            if exact is not None:output[code]=exact;self.cache_hits+=1
            else:pending.append(code)
        size=max(1,int(batch_size or self.config.get("asof_batch_size",20)))
        start=f"{trade_date.isoformat()} 09:30:00";end=f"{trade_date.isoformat()} {cutoff.isoformat()}"
        for offset in range(0,len(pending),size):
            batch=pending[offset:offset+size];requested=self.now();started=time.perf_counter();error=None;retry_count=0;maximum_retries=max(0,int(self.config.get("asof_retry_count",0)))
            while True:
                try:
                    values=self.provider.get_minute_bars_batch(batch,start,end,"1m");break
                except Exception as exc:
                    category=getattr(exc,"category",None);error=str(getattr(category,"value",category) or type(exc).__name__)
                    retryable=error in {"RATE_LIMITED","TIMEOUT","NETWORK_ERROR","PROVIDER_ERROR"}
                    if not retryable or retry_count>=maximum_retries:values={code:[] for code in batch};break
                    retry_count+=1;time.sleep(1)
            received=self.now();self.provider_calls+=1+retry_count
            self.audit.append({"batch_id":f"hf-{len(self.audit)+1:03d}","codes_requested":batch,"codes_returned":[c for c,r in values.items() if r],"rows":sum(len(r) for r in values.values()),"latency_ms":round((time.perf_counter()-started)*1000),"errorcode":error,"retry_count":retry_count,"request_hash":_hash([batch,start,end]),"requested_at":requested.isoformat(),"received_at":received.isoformat()})
            for code in batch:
                snapshot,bars=self.reconstruct(code,values.get(code,[]),trade_date,cutoff,previous_closes.get(code),requested,received)
                output[code]=snapshot;series[code]=bars
        return output,series

    def reconstruct(self, code: str, rows: list[Any], trade_date: date, cutoff: clock_time, previous_close: float | None, requested_at: datetime, received_at: datetime) -> tuple[AsOfSnapshot,list[dict[str,Any]]]:
        cutoff_dt=datetime.combine(trade_date,cutoff,tzinfo=SHANGHAI);start_dt=datetime.combine(trade_date,clock_time(9,30),tzinfo=SHANGHAI)
        unique={};duplicate_conflict=False;suspended=any(str(getattr(row,"data_status","")).upper()=="SUSPENDED" for row in rows)
        for row in rows:
            stamp=_stamp(getattr(row,"datetime",None))
            if stamp and stamp>cutoff_dt:self.excluded_post_cutoff_rows+=1;continue
            if stamp and start_dt<=stamp<=cutoff_dt:
                if stamp in unique and _bar_signature(unique[stamp])!=_bar_signature(row):duplicate_conflict=True
                unique[stamp]=row
        ordered=sorted(unique.items());bars=[]
        for stamp,row in ordered:
            bars.append({"bar_time":stamp.isoformat(),"open":_num(row.open),"high":_num(row.high),"low":_num(row.low),"close":_num(row.close),"volume":_num(row.volume),"amount":_num(row.amount)})
        if not bars:
            missing=self._missing(code,cutoff_dt,requested_at,received_at,"SUSPENDED" if suspended else "NO_ELIGIBLE_MINUTE_BARS")
            return AsOfSnapshot(**{**missing.to_dict(),"data_quality":"SUSPENDED"}) if suspended else missing,[]
        last=datetime.fromisoformat(bars[-1]["bar_time"]);delay=(cutoff_dt-last).total_seconds();critical=all(all(_num(row.get(k)) is not None for k in ("open","high","low","close")) for row in bars)
        expected=_expected_count(start_dt,cutoff_dt);missing_count=max(0,expected-len(bars));tolerance=float(self.config.get("near_cutoff_tolerance_seconds",180))
        quality="DATA_INSUFFICIENT" if duplicate_conflict or not critical or delay>tolerance else "PARTIAL" if missing_count else "VALID_EXACT" if delay==0 else "VALID_NEAR_CUTOFF"
        volume_semantics=str(self.config.get("volume_semantics","UNKNOWN"));amount_semantics=str(self.config.get("amount_semantics","UNKNOWN"))
        volume=_aggregate(bars,"volume",volume_semantics);amount=_aggregate(bars,"amount",amount_semantics)
        vwap=amount/volume if volume and amount and volume_semantics=="INCREMENTAL_SHARES" and amount_semantics=="INCREMENTAL_CNY" else None
        if vwap is None and quality.startswith("VALID"):quality="PARTIAL"
        close=bars[-1]["close"];opening=bars[0]["open"];high=max(row["high"] for row in bars);low=min(row["low"] for row in bars)
        provenance={"provider":"IFIND_HTTP","requested_at":requested_at.isoformat(),"received_at":received_at.isoformat(),"provider_payload_time":getattr(rows[-1],"datetime",None) if rows else None,"exchange_event_time":bars[-1]["bar_time"],"last_eligible_bar_time":bars[-1]["bar_time"],"cutoff_time":cutoff_dt.isoformat(),"time_semantics":"BAR_END_TIME","cutoff_compliant":last<=cutoff_dt,"reconstruction_method":"IFIND_HIGH_FREQUENCY_1M","volume_semantics":volume_semantics,"amount_semantics":amount_semantics,"duplicate_conflict":duplicate_conflict}
        return AsOfSnapshot(code,opening,high,low,close,volume,amount,vwap,(close/previous_close-1) if previous_close else None,(high/low-1) if low else None,bars[-1]["bar_time"],len(bars),missing_count,quality,"IFIND_HIGH_FREQUENCY_1M",last<=cutoff_dt,provenance),bars

    def _exact_snapshot(self,trade_date,cutoff,code,previous_close):
        rows=self.session.scalars(select(MarketSnapshotShadow).where(MarketSnapshotShadow.stock_code==code,MarketSnapshotShadow.provider=="IFIND_HTTP").order_by(MarketSnapshotShadow.snapshot_time.desc())).all()
        cutoff_dt=datetime.combine(trade_date,cutoff,tzinfo=SHANGHAI)
        for row in rows:
            stamp=_stamp(row.provider_time)
            if stamp is None:continue
            if stamp.date()==trade_date and stamp<=cutoff_dt and (cutoff_dt-stamp).total_seconds()<=180:
                latest=_num(row.latest);low=_num(row.low);high=_num(row.high)
                return AsOfSnapshot(code,_num(row.open),high,low,latest,_num(row.volume),_num(row.amount),None,(latest/previous_close-1) if latest and previous_close else None,(high/low-1) if high and low else None,stamp.isoformat(),0,0,"PARTIAL","EXACT_PERSISTED_SNAPSHOT",True,{"provider":"IFIND_HTTP","provider_payload_time":row.provider_time,"exchange_event_time":stamp.isoformat(),"cutoff_time":cutoff_dt.isoformat(),"cutoff_compliant":True,"reconstruction_method":"EXACT_PERSISTED_SNAPSHOT"})
        return None

    @staticmethod
    def _missing(code,cutoff,requested,received,reason):
        return AsOfSnapshot(code,None,None,None,None,None,None,None,None,None,None,0,0,"DATA_INSUFFICIENT","FAILED",False,{"requested_at":requested.isoformat(),"received_at":received.isoformat(),"cutoff_time":cutoff.isoformat(),"reason":reason,"cutoff_compliant":False})


def cross_section_breadth(snapshots: dict[str,AsOfSnapshot]) -> dict[str,Any]:
    values=[row.change_pct_to_cutoff for row in snapshots.values() if row.change_pct_to_cutoff is not None and row.data_quality in {"VALID_EXACT","VALID_NEAR_CUTOFF"}]
    return {"scope":"QUANT_TOP100_CROSS_SECTION","valid_count":len(values),"positive_count":sum(v>0 for v in values),"negative_count":sum(v<0 for v in values),"flat_count":sum(v==0 for v in values),"advancing_ratio":sum(v>0 for v in values)/len(values) if values else None,"declining_ratio":sum(v<0 for v in values)/len(values) if values else None,"equal_weight_return":sum(values)/len(values) if values else None,"median_return":median(values) if values else None,"below_3_count":sum(v<=-.03 for v in values),"below_5_count":sum(v<=-.05 for v in values)}


def intraday_series_metrics(snapshot:AsOfSnapshot,bars:list[dict[str,Any]],*,vwap_comparable:bool=True)->dict[str,Any]:
    closes=[(_stamp(row.get("bar_time")),_num(row.get("close"))) for row in bars]
    closes=[(stamp,value) for stamp,value in closes if stamp is not None and value is not None]
    def at(hour,minute):
        eligible=[value for stamp,value in closes if stamp.time()<=clock_time(hour,minute)]
        return eligible[-1] if eligible else None
    first=at(9,30);ten30=at(10,30);eleven=at(11,0);last=closes[-1][1] if closes else None
    returns=[closes[i][1]/closes[i-1][1]-1 for i in range(1,len(closes)) if closes[i-1][1]]
    return {"morning_return":snapshot.change_pct_to_cutoff,"morning_drawdown":(last/snapshot.session_high-1) if last and snapshot.session_high else None,"price_vs_vwap":(last/snapshot.vwap_to_cutoff-1) if vwap_comparable and last and snapshot.vwap_to_cutoff else None,"vwap_semantic_status":"VALID" if vwap_comparable and snapshot.vwap_to_cutoff else "DATA_INSUFFICIENT_INDEX_LEVEL_NOT_COMPARABLE" if not vwap_comparable else "DATA_INSUFFICIENT","first_hour_return":ten30/first-1 if ten30 and first else None,"second_hour_return":last/ten30-1 if last and ten30 else None,"last_30m_return":last/eleven-1 if last and eleven else None,"minute_trend":last/first-1 if last and first else None,"intraday_volatility":pstdev(returns) if len(returns)>1 else None,"breadth_component_available":snapshot.change_pct_to_cutoff is not None}


def _aggregate(rows,key,semantics):
    values=[_num(row.get(key)) for row in rows if _num(row.get(key)) is not None]
    if not values:return None
    if semantics.startswith("INCREMENTAL_"):return sum(values)
    if semantics.startswith("CUMULATIVE_"):return values[-1]
    return None
def _expected_count(start,end):
    morning_end=datetime.combine(start.date(),clock_time(11,30),tzinfo=start.tzinfo);afternoon_start=datetime.combine(start.date(),clock_time(13,0),tzinfo=start.tzinfo)
    morning=max(0,int((min(end,morning_end)-start).total_seconds()//60)+1)
    afternoon=max(0,int((end-afternoon_start).total_seconds()//60)+1) if end>=afternoon_start else 0
    return morning+afternoon
def _bar_signature(row):return tuple(_num(getattr(row,key,None)) for key in ("open","high","low","close","volume","amount"))
def _stamp(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"));return (parsed if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI)).astimezone(SHANGHAI)
    except (TypeError,ValueError):return None
def _num(value):
    try:return float(value) if value is not None else None
    except (TypeError,ValueError):return None
def _hash(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(",",":")).encode()).hexdigest()
