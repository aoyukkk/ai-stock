from fastapi.testclient import TestClient
from pathlib import Path

from backend.main import app


ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def test_manual_status_returns_manual_mode() -> None:
    response = client.get("/api/manual/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["execution_mode"] == "manual"
    assert payload["data"]["scheduler_enabled"] is False


def test_datasource_providers_returns_debug_providers() -> None:
    response = client.get("/api/datasource/providers")

    names = {provider["name"] for provider in response.json()["data"]["providers"]}
    assert {"mock", "tushare", "akshare", "baostock", "ths_stub"}.issubset(names)


def test_datasource_mock_stock_list_returns_data() -> None:
    response = client.get("/api/datasource/stock-list?provider=mock")

    payload = response.json()
    assert payload["success"] is True
    assert len(payload["data"]["stocks"]) >= 10


def test_datasource_mock_kline_returns_data() -> None:
    response = client.get("/api/datasource/kline?provider=mock&stock_code=000001")

    payload = response.json()
    assert payload["success"] is True
    assert len(payload["data"]["bars"]) >= 30


def test_datasource_mock_realtime_returns_data() -> None:
    response = client.get("/api/datasource/realtime?provider=mock&stock_code=000001")

    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["stock_code"] == "000001"


def test_debug_html_exists() -> None:
    assert (ROOT / "frontend" / "debug" / "index.html").is_file()
