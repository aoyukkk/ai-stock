from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def test_manual_real_quant_api_runs_with_mock_provider(tmp_path) -> None:
    response = client.post(
        "/api/pools/run-quant-real",
        json={
            "provider": "mock",
            "history_provider": "mock",
            "top_n": 5,
            "sample_limit": 5,
            "save_to_db": False,
            "output": str(tmp_path / "api_quant_report.json"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"success", "data", "error", "trace_id"}
    assert payload["success"] is True
    assert payload["error"] is None
    assert payload["data"]["top_count"] == min(5, payload["data"]["scored_count"])
    assert payload["data"]["no_llm_call_verified"] is True
    assert "performance" in payload["data"]


def test_manual_real_quant_api_accepts_performance_options(tmp_path) -> None:
    response = client.post(
        "/api/pools/run-quant-real",
        json={
            "provider": "mock",
            "history_provider": "mock",
            "top_n": 5,
            "sample_limit": 5,
            "use_cache": False,
            "refresh_cache": True,
            "data_fetch_workers": 2,
            "factor_workers": "auto",
            "save_to_db": False,
            "output": str(tmp_path / "api_quant_perf_report.json"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["performance"]["data_fetch_workers"] == 2
    assert payload["data"]["performance"]["factor_compute_workers"] == 1


def test_manual_real_quant_api_error_uses_v03_envelope() -> None:
    response = client.post(
        "/api/pools/run-quant-real",
        json={"provider": "unknown", "history_provider": "mock"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert payload["error"]["message"]
