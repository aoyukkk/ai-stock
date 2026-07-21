from __future__ import annotations
from datetime import date,datetime,time,timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from midday.asof import AsOfMarketDataResolver,cross_section_breadth,intraday_series_metrics

SH=ZoneInfo("Asia/Shanghai")
def _row(stamp,volume=100,amount=1000,close=10):return SimpleNamespace(datetime=stamp,open=10,high=10.2,low=9.8,close=close,volume=volume,amount=amount,data_status="AVAILABLE_DELAYED")
def _full_rows(close=10):
    start=datetime(2026,7,20,9,30)
    return [_row((start+timedelta(minutes=i)).isoformat(sep=' '),close=close) for i in range(121)]
def _resolver(config=None):return AsOfMarketDataResolver(SimpleNamespace(scalars=lambda *_:SimpleNamespace(all=lambda:[])),None,{"volume_semantics":"INCREMENTAL_SHARES","amount_semantics":"INCREMENTAL_CNY","near_cutoff_tolerance_seconds":180,**(config or {})})

def test_requested_after_cutoff_and_exact_last_bar_are_legal():
    rows=_full_rows();rows[-1]=_row("2026-07-20 11:30",200,2200,11)
    snap,bars=_resolver().reconstruct("000001.SZ",rows,date(2026,7,20),time(11,30),9,datetime(2026,7,20,11,50,tzinfo=SH),datetime(2026,7,20,11,51,tzinfo=SH))
    assert snap.data_quality=="VALID_EXACT" and snap.cutoff_compliant and len(bars)==121
    assert snap.vwap_to_cutoff==pytest.approx((120*1000+2200)/(120*100+200)) and snap.provenance["requested_at"].startswith("2026-07-20T11:50")

def test_late_bar_excluded_and_early_last_bar_partial():
    rows=[_row("2026-07-20 09:30"),_row("2026-07-20 11:26"),_row("2026-07-20 13:01")]
    resolver=_resolver();snap,bars=resolver.reconstruct("000001.SZ",rows,date(2026,7,20),time(11,30),9,datetime.now(SH),datetime.now(SH))
    assert snap.data_quality=="DATA_INSUFFICIENT" and bars[-1]["bar_time"].startswith("2026-07-20T11:26") and resolver.excluded_post_cutoff_rows==1

def test_unsorted_duplicate_bars_are_sorted_and_deduplicated():
    rows=[_row("2026-07-20 11:30",close=11),_row("2026-07-20 09:30"),_row("2026-07-20 11:30",close=11)]
    snap,bars=_resolver().reconstruct("000001.SZ",rows,date(2026,7,20),time(11,30),9,datetime.now(SH),datetime.now(SH))
    assert len(bars)==2 and snap.close_at_cutoff==11

def test_conflicting_duplicate_bars_are_data_insufficient():
    rows=[_row("2026-07-20 11:30",close=11),_row("2026-07-20 11:30",close=12)]
    snap,_=_resolver().reconstruct("000001.SZ",rows,date(2026,7,20),time(11,30),9,datetime.now(SH),datetime.now(SH))
    assert snap.data_quality=="DATA_INSUFFICIENT" and snap.provenance["duplicate_conflict"]

def test_missing_intraday_bar_is_partial_even_with_exact_cutoff():
    rows=[_row("2026-07-20 09:30"),_row("2026-07-20 11:30")]
    snap,_=_resolver().reconstruct("000001.SZ",rows,date(2026,7,20),time(11,30),9,datetime.now(SH),datetime.now(SH))
    assert snap.data_quality=="PARTIAL" and snap.missing_bar_count==119

def test_suspended_status_is_not_reported_as_provider_failure():
    row=_row("2026-07-20 08:00");row.data_status="SUSPENDED"
    snap,_=_resolver().reconstruct("000001.SZ",[row],date(2026,7,20),time(11,30),9,datetime.now(SH),datetime.now(SH))
    assert snap.data_quality=="SUSPENDED" and snap.provenance["reason"]=="SUSPENDED"

def test_index_metrics_do_not_claim_trade_value_vwap_is_index_level():
    rows=[_row("2026-07-20 09:30",close=10),_row("2026-07-20 10:30",close=11),_row("2026-07-20 11:00",close=10.5),_row("2026-07-20 11:30",close=10.8)]
    snap,bars=_resolver().reconstruct("000001.SH",rows,date(2026,7,20),time(11,30),9,datetime.now(SH),datetime.now(SH))
    metrics=intraday_series_metrics(snap,bars,vwap_comparable=False)
    assert metrics["price_vs_vwap"] is None
    assert metrics["vwap_semantic_status"]=="DATA_INSUFFICIENT_INDEX_LEVEL_NOT_COMPARABLE"
    assert metrics["first_hour_return"]==pytest.approx(.1)

def test_incremental_cumulative_and_unknown_volume_semantics():
    rows=[_row("2026-07-20 09:30",100,1000),_row("2026-07-20 11:30",200,2400)]
    inc,_=_resolver().reconstruct("A",rows,date(2026,7,20),time(11,30),9,datetime.now(SH),datetime.now(SH));assert inc.volume_to_cutoff==300
    cumulative,_=_resolver({"volume_semantics":"CUMULATIVE_SHARES","amount_semantics":"CUMULATIVE_CNY"}).reconstruct("A",rows,date(2026,7,20),time(11,30),9,datetime.now(SH),datetime.now(SH));assert cumulative.volume_to_cutoff==200 and cumulative.vwap_to_cutoff is None
    unknown,_=_resolver({"volume_semantics":"UNKNOWN"}).reconstruct("A",rows,date(2026,7,20),time(11,30),9,datetime.now(SH),datetime.now(SH));assert unknown.vwap_to_cutoff is None and unknown.data_quality=="PARTIAL"

def test_cross_section_scope_is_not_full_a():
    rows=[_resolver().reconstruct(code,_full_rows(close),date(2026,7,20),time(11,30),10,datetime.now(SH),datetime.now(SH))[0] for code,close in (("A",11),("B",9))]
    result=cross_section_breadth({row.code:row for row in rows});assert result["scope"]=="QUANT_TOP100_CROSS_SECTION" and result["valid_count"]==2

def test_afternoon_expected_count_excludes_lunch_break():
    rows=_full_rows()+[_row(f"2026-07-20 13:0{i}") for i in range(6)]
    snap,bars=_resolver().reconstruct("000001.SZ",rows,date(2026,7,20),time(13,5),9,datetime.now(SH),datetime.now(SH))
    assert len(bars)==127 and snap.missing_bar_count==0 and snap.data_quality=="VALID_EXACT"

def test_provider_error_category_is_audited_without_secret():
    class FailedProvider:
        def get_minute_bars_batch(self,*_args):
            from datasource.ifind.http.errors import IFindHttpError,IFindHttpErrorCategory
            raise IFindHttpError(IFindHttpErrorCategory.PROVIDER_ERROR,"safe")
    resolver=AsOfMarketDataResolver(SimpleNamespace(scalars=lambda *_:SimpleNamespace(all=lambda:[])),FailedProvider(),{"asof_retry_count":0,"volume_semantics":"INCREMENTAL_SHARES","amount_semantics":"INCREMENTAL_CNY"})
    resolver.resolve(date(2026,7,20),time(13,5),["000001.SZ"],previous_closes={"000001.SZ":10})
    assert resolver.audit[0]["errorcode"]=="PROVIDER_ERROR" and resolver.audit[0]["retry_count"]==0

def test_persisted_snapshot_uses_provider_event_time_not_fetch_time():
    stale=SimpleNamespace(provider_time="2026-07-20 11:30:00",snapshot_time=datetime(2026,7,20,13,5,tzinfo=SH),latest=10,open=10,high=10,low=10,volume=1,amount=10)
    session=SimpleNamespace(scalars=lambda *_:SimpleNamespace(all=lambda:[stale]))
    resolver=AsOfMarketDataResolver(session,None,{})
    assert resolver._exact_snapshot(date(2026,7,20),time(13,5),"000001.SZ",9) is None
