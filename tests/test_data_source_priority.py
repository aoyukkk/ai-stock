from __future__ import annotations

import yaml

from datasource.registry import create_default_registry


def test_data_source_priority_uses_tushare_primary_and_baostock_backup() -> None:
    with open("config/data_sources.yaml", "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)["data_sources"]

    assert config["market_primary"]["provider"] == "tushare"
    assert config["market_primary"]["enabled"] is True
    assert config["market_history"]["provider"] == "tushare"
    assert config["market_backup"][0]["provider"] == "baostock"
    assert config["market_backup"][0]["enabled"] is True
    assert config["akshare"]["enabled"] is False


def test_registry_status_reflects_data_source_priority() -> None:
    statuses = {provider.name: provider.health_check() for provider in create_default_registry().list_providers()}

    assert statuses["tushare"].enabled is True
    assert statuses["baostock"].enabled is True
    assert statuses["akshare"].enabled is False
    assert statuses["mock"].enabled is True
