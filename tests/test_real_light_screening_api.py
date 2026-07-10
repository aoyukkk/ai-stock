from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def test_screening_api_defaults_to_dry_run_and_zero_real_call() -> None:
    response = client.post("/api/pools/run-llm-screening", json={})
    payload = response.json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["data"]["status"] == "DRY_RUN"
    assert payload["data"]["dry_run"] is True
    assert payload["data"]["sample_size"] <= 3
    assert payload["data"]["results"] == []
    assert payload["data"]["route"]["model_alias"] == "light-screening-default"


def test_screening_api_rejects_sample_size_over_five() -> None:
    response = client.post(
        "/api/pools/run-llm-screening",
        json={"sample_size": 6},
    )
    assert response.status_code == 422


def test_screening_api_forbids_model_ids_and_keys() -> None:
    for extra in (
        {"model": "deepseek-v4-flash"},
        {"model_alias": "light-screening-default"},
        {"api_key": "not-allowed"},
    ):
        response = client.post("/api/pools/run-llm-screening", json=extra)
        assert response.status_code == 422
