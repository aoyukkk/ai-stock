from __future__ import annotations

import sys
import types
from pathlib import Path

import yaml

from datasource.tushare_provider import TushareMarketDataProvider


ROOT = Path(__file__).resolve().parents[1]


def test_tushare_token_placeholders_are_blank() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    env = (ROOT / ".env").read_text(encoding="utf-8")
    config = yaml.safe_load((ROOT / "config" / "data_sources.yaml").read_text(encoding="utf-8"))

    assert "TUSHARE_TOKEN=" in env_example
    assert "TUSHARE_TOKEN=fake" not in env_example
    assert "TUSHARE_TOKEN=" in env
    assert config["data_sources"]["tushare"]["token_env"] == "TUSHARE_TOKEN"
    assert "token_env" in config["data_sources"]["tushare"]
    assert "token" not in {key.lower(): value for key, value in config["data_sources"]["tushare"].items() if key != "token_env"}


def test_tushare_provider_redacts_token_from_errors(monkeypatch, tmp_path) -> None:
    fake_token = "fake-token-for-redaction"

    class ErrorFakePro:
        def stock_basic(self, **kwargs):
            raise RuntimeError(f"upstream failed with {fake_token}")

    monkeypatch.setenv("TUSHARE_TOKEN", fake_token)
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: ErrorFakePro()))

    provider = TushareMarketDataProvider(cache_dir=tmp_path, cache_enabled=False, request_interval_seconds=0)
    result = provider.query_endpoint("stock_basic")

    assert result.status == "error"
    assert fake_token not in (result.error_message or "")
