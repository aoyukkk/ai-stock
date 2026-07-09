from __future__ import annotations

import json
import sys
import types

from scripts import smoke_tushare_data_source as smoke


class SmokeFakePro:
    def stock_basic(self, **kwargs):
        return [{"ts_code": "000001.SZ", "symbol": "000001", "name": "Ping An Bank"}]

    def trade_cal(self, **kwargs):
        return [{"exchange": "SSE", "cal_date": "20260105", "is_open": "1"}]

    def daily(self, **kwargs):
        return [{"ts_code": "000001.SZ", "trade_date": "20260105", "open": 10, "high": 11, "low": 9, "close": 10}]

    def moneyflow(self, **kwargs):
        return [{"ts_code": "000001.SZ", "trade_date": "20260105", "net_mf_amount": 10}]

    def query(self, api_name, **kwargs):
        return []

    def __getattr__(self, name):
        return lambda **kwargs: []


def test_tushare_smoke_script_generates_endpoint_statuses(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: SmokeFakePro()))

    output = tmp_path / "smoke.json"
    report = smoke.run_smoke(limit=2, output=output)
    persisted = json.loads(output.read_text(encoding="utf-8"))

    assert report["token_configured"] is True
    assert persisted["results"]["stock_basic"]["status"] == "available"
    assert persisted["results"]["trade_cal"]["status"] == "available"
    assert persisted["results"]["daily"]["status"] == "available"
    assert "fake-token" not in output.read_text(encoding="utf-8")
