from fastapi.testclient import TestClient

from backend.main import create_app
from scripts.run_final_smoke import SMOKE_ENDPOINTS, run_smoke


def test_final_smoke_all_core_apis_return_unified_envelope() -> None:
    client = TestClient(create_app())
    results = run_smoke(client)

    assert len(results) == len(SMOKE_ENDPOINTS)
    assert all(result["passed"] for result in results), results
    assert all(result["trace_id"] for result in results)
