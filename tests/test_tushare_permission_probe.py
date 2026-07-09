from __future__ import annotations

import json
import sys
import types

from scripts import probe_tushare_permissions as probe


class TinyFakePro:
    def stock_basic(self, **kwargs):
        return [{"ts_code": "000001.SZ", "name": "Ping An Bank"}]

    def trade_cal(self, **kwargs):
        return [{"cal_date": "20260105", "is_open": "1"}]

    def daily(self, **kwargs):
        return [{"ts_code": "000001.SZ", "trade_date": "20260105", "open": 10, "high": 11, "low": 9, "close": 10}]

    def query(self, api_name, **kwargs):
        if api_name == "cyq_chips":
            raise RuntimeError("没有权限")
        return []

    def __getattr__(self, name):
        def method(**kwargs):
            if name == "cyq_chips":
                raise RuntimeError("没有权限")
            return []

        return method


def test_permission_probe_without_token_generates_report(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    report = probe.run_probe(output=tmp_path / "probe.json")

    assert report["token_configured"] is False
    assert "TUSHARE_TOKEN is not configured" in report["recommendations"]
    assert (tmp_path / "probe.json").is_file()


def test_permission_probe_masks_token_and_classifies_results(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: TinyFakePro()))

    output = tmp_path / "probe.json"
    report = probe.run_probe(output=output)
    text = output.read_text(encoding="utf-8")

    assert report["token_configured"] is True
    assert "stock_basic" in report["available_apis"]
    assert "cyq_chips" in report["permission_errors"]
    assert "fake-token" not in text
    assert json.loads(text)["report_path"] == str(output)
