import sys
import types

import pytest

from datasource.baostock_provider import BaoStockBatchSession, BaoStockMarketDataProvider


class FakeLoginResult:
    error_code = "0"
    error_msg = ""


def test_baostock_batch_session_logs_in_and_out(monkeypatch, tmp_path) -> None:
    calls = {"login": 0, "logout": 0}
    fake = types.SimpleNamespace(
        login=lambda: calls.__setitem__("login", calls["login"] + 1) or FakeLoginResult(),
        logout=lambda: calls.__setitem__("logout", calls["logout"] + 1),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    provider = BaoStockMarketDataProvider(cache_dir=tmp_path)

    with BaoStockBatchSession(provider) as session_provider:
        assert session_provider is provider
        assert provider._session_bs is fake

    assert calls == {"login": 1, "logout": 1}
    assert provider._session_bs is None


def test_baostock_batch_session_logs_out_on_exception(monkeypatch, tmp_path) -> None:
    calls = {"login": 0, "logout": 0}
    fake = types.SimpleNamespace(
        login=lambda: calls.__setitem__("login", calls["login"] + 1) or FakeLoginResult(),
        logout=lambda: calls.__setitem__("logout", calls["logout"] + 1),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    provider = BaoStockMarketDataProvider(cache_dir=tmp_path)

    with pytest.raises(RuntimeError):
        with provider.batch_session():
            raise RuntimeError("boom")

    assert calls == {"login": 1, "logout": 1}
    assert provider._session_bs is None
