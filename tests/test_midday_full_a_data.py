from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace

import pytest

from midday.full_a_data import FullAAsOfResolver, FullADataCapacityError


CFG={"snapshot_batch_size":1000,"minute_reconstruction_batch_size":60,"maximum_provider_calls":30,"validation_sample_count":2,"minimum_full_a_coverage":.95}


class Client:
    call_count=0


class Provider:
    def __init__(self,missing=()):self.client=Client();self.missing=set(missing)
    def get_historical_snapshots(self,codes,asof):
        self.client.call_count+=1
        return [SimpleNamespace(stock_code=code,datetime=asof,open=10,high=11,low=9,latest=10,pre_close=10,volume=12100,amount=121000) for code in codes if code not in self.missing]
    def get_minute_bars_batch(self,codes,start,end,interval):
        self.client.call_count+=1;output={}
        for code in codes:
            output[code]=[SimpleNamespace(stock_code=code,datetime=f"2026-07-20 {9+(i+30)//60:02d}:{(i+30)%60:02d}:00",open=10,high=11 if i==120 else 10,low=9 if i==0 else 10,close=10,volume=100,amount=1000,data_status="AVAILABLE") for i in range(121)]
        return output


def test_validated_snapshot_method_is_used():
    result=FullAAsOfResolver(Provider(),CFG).resolve(date(2026,7,20),time(11,30),["000001.SZ","000002.SZ"],previous_closes={"000001.SZ":10,"000002.SZ":10})
    assert result.validation["passed"] and result.method=="LUNCH_BREAK_FROZEN_SNAPSHOT_VALIDATED"
def test_snapshot_failure_enters_minute_reconstruction():
    result=FullAAsOfResolver(Provider({"688001.SH"}),CFG).resolve(date(2026,7,20),time(11,30),["000001.SZ","688001.SH"],previous_closes={"000001.SZ":10,"688001.SH":10})
    assert result.snapshots["688001.SH"]["reconstruction_method"]=="IFIND_HIGH_FREQUENCY_1M"
def test_missing_snapshot_never_silently_returns_top100():
    codes=[f"{i:06d}.SZ" for i in range(150)];result=FullAAsOfResolver(Provider(set(codes[100:])),CFG).resolve(date(2026,7,20),time(11,30),codes,previous_closes={code:10 for code in codes})
    assert result.returned==150
def test_capacity_failure_is_explicit():
    config={**CFG,"minute_reconstruction_batch_size":1,"maximum_provider_calls":3};codes=[f"{i:06d}.SZ" for i in range(5)]
    with pytest.raises(FullADataCapacityError,match="FULL_A_DATA_CAPACITY_FAILED"):FullAAsOfResolver(Provider(set(codes)),config).resolve(date(2026,7,20),time(11,30),codes,previous_closes={code:10 for code in codes})
def test_capacity_forecast_blocks_full_a_before_partial_requests():
    provider=Provider();config={**CFG,"snapshot_batch_size":100,"minute_reconstruction_batch_size":60,"maximum_provider_calls":30};codes=[f"{i:06d}.SZ" for i in range(5000)]
    with pytest.raises(FullADataCapacityError,match="snapshot_calls=51:minute_batch_size=60:minute_calls=84"):FullAAsOfResolver(provider,config).resolve(date(2026,7,20),time(11,30),codes,previous_closes={code:10 for code in codes})
    assert provider.client.call_count==0
def test_asof_rows_are_cutoff_compliant():
    result=FullAAsOfResolver(Provider(),CFG).resolve(date(2026,7,20),time(11,30),["000001.SZ"],previous_closes={"000001.SZ":10})
    assert result.snapshots["000001.SZ"]["cutoff_compliant"]
def test_provider_audit_has_no_token_fields():
    result=FullAAsOfResolver(Provider(),CFG).resolve(date(2026,7,20),time(11,30),["000001.SZ"],previous_closes={"000001.SZ":10})
    assert all("token" not in str(row).lower() for row in result.audit)
def test_snapshot_and_minute_consistency_records_sample_count():
    result=FullAAsOfResolver(Provider(),CFG).resolve(date(2026,7,20),time(11,30),["000001.SZ","000002.SZ"],previous_closes={"000001.SZ":10,"000002.SZ":10})
    assert result.validation["sample_count"]==2 and result.validation["consistent_count"]==2
def test_minute_batches_are_bounded():
    codes=[f"{i:06d}.SZ" for i in range(61)];resolver=FullAAsOfResolver(Provider(),CFG);values,_=resolver.fetch_minutes(date(2026,7,20),time(11,30),codes,maximum_additional_calls=2)
    assert len(values)==61 and resolver.hf_batches==2
def test_trigger_minute_capacity_is_checked_before_calls():
    resolver=FullAAsOfResolver(Provider(),CFG)
    with pytest.raises(FullADataCapacityError):resolver.fetch_minutes(date(2026,7,20),time(11,30),[f"{i:06d}.SZ" for i in range(61)],maximum_additional_calls=1)
