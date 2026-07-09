import os
import sys
import types

import pytest

from datasource.akshare_provider import AKShareMarketDataProvider
from datasource.exceptions import DataSourceError


class FakeFrame:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def to_dict(self, orient: str = "records") -> list[dict]:
        assert orient == "records"
        return self.records


def test_akshare_import_does_not_request_network(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "akshare", raising=False)

    provider = AKShareMarketDataProvider()

    assert provider.name == "akshare"
    assert provider.diagnostics()["source_status"] == "not_requested"


def test_akshare_proxy_error_gives_clear_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:7890")
    fake = types.SimpleNamespace(
        stock_zh_a_spot_em=lambda: (_ for _ in ()).throw(
            RuntimeError("ProxyError: remote end closed connection without response")
        )
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)

    provider = AKShareMarketDataProvider(cache_dir=tmp_path)
    with pytest.raises(DataSourceError, match="ProxyError"):
        provider.get_stock_list()

    diagnostics = provider.diagnostics()
    assert diagnostics["error_type"] == "ProxyError"
    assert diagnostics["proxy_env_detected"] is True


def test_akshare_no_proxy_mode_does_not_crash_and_restores_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:7890")
    observed = {}

    def stock_zh_a_spot_em():
        observed["https_proxy_inside_call"] = os.environ.get("HTTPS_PROXY")
        return FakeFrame([{"\u4ee3\u7801": "000001", "\u540d\u79f0": "Ping An Bank"}])

    fake = types.SimpleNamespace(stock_zh_a_spot_em=stock_zh_a_spot_em)
    monkeypatch.setitem(sys.modules, "akshare", fake)

    provider = AKShareMarketDataProvider(cache_dir=tmp_path, proxy_mode="no_proxy")
    stocks = provider.get_stock_list()

    assert stocks[0].code == "000001"
    assert observed["https_proxy_inside_call"] is None
    assert os.environ["HTTPS_PROXY"] == "http://proxy.example:7890"
