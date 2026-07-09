import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import load_app_config
from backend.core.config_manager import ConfigManager, ConfigManagerError
from backend.main import create_app
from scripts.check_security_config import run_checks


ROOT = Path(__file__).resolve().parents[1]
client = TestClient(create_app())


def test_final_safety_switches_remain_locked_down() -> None:
    config = load_app_config()
    frontend_env = (ROOT / "frontend" / ".env.example").read_text(encoding="utf-8")

    assert config.real_trading_enabled is False
    assert config.config_files["models"]["llm"]["mock_only"] is True
    assert "VITE_ENABLE_REAL_TRADING=false" in frontend_env
    assert "ENABLE_REAL_TRADING=false" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_mock_provider_and_no_real_broker_are_default() -> None:
    data_sources = client.get("/api/v1/data-sources/status").json()
    llm = client.get("/api/v1/llm/status").json()
    virtual = load_app_config().config_files["virtual_trading"]["virtual_trading"]

    providers = data_sources["data"]["providers"]
    enabled_real_providers = [
        provider
        for provider in providers
        if provider["name"] != "mock" and provider["enabled"] is True
    ]

    assert {provider["name"] for provider in enabled_real_providers}.issubset({"akshare"})
    assert any(provider["name"] == "mock" and provider["enabled"] for provider in providers)
    assert llm["data"]["mock_only"] is True
    assert llm["data"]["default_provider"] == "mock"
    assert virtual["broker"]["allow_real_broker"] is False


def test_frontend_has_no_real_order_buttons_and_virtual_labels() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "frontend" / "src").rglob("*")
        if path.suffix in {".vue", ".ts"}
    )

    forbidden = ("实盘下单", "Place Real Order", "Submit Real Order", "Connect Broker")
    for phrase in forbidden:
        assert phrase not in source
    assert "AI Simulation" in source
    assert "Virtual" in source or "virtual" in source


def test_core_apis_do_not_expose_sensitive_fields() -> None:
    endpoints = (
        "/health",
        "/api/v1/system/config-summary",
        "/api/v1/data-sources/status",
        "/api/v1/llm/status",
        "/api/v1/config/effective",
    )
    forbidden = ("api_key", "password", "secret", "credential", "username")
    for endpoint in endpoints:
        payload = client.get(endpoint).json()
        text = json.dumps(payload, ensure_ascii=False).lower()
        for term in forbidden:
            assert term not in text


def test_packaging_and_electron_remain_safe() -> None:
    for path in (
        ROOT / "packaging" / "pyinstaller_backend.spec",
        ROOT / "packaging" / "electron-builder.config.js",
        ROOT / "frontend" / "electron-builder.config.js",
    ):
        assert ".env" not in path.read_text(encoding="utf-8")

    preload = (ROOT / "frontend" / "electron" / "preload.ts").read_text(encoding="utf-8")
    main = (ROOT / "frontend" / "electron" / "main.ts").read_text(encoding="utf-8")
    assert "exec" not in preload
    assert "shell.open" not in preload
    assert "nodeIntegration: false" in main
    assert "contextIsolation: true" in main


def test_security_check_passes_and_config_manager_blocks_real_trading() -> None:
    report = run_checks(ROOT)
    assert report.ok, report.errors

    manager = ConfigManager()
    with pytest.raises(ConfigManagerError) as exc_info:
        manager.set_config_value(
            "paper_trading.real_trading_enabled",
            True,
            user="safety_test",
            reason="must be blocked",
        )
    assert exc_info.value.code == "CONFIG_KEY_NOT_EDITABLE"
