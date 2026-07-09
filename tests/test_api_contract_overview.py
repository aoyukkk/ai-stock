from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_api_contract_overview_doc_exists_and_lists_core_modules() -> None:
    path = ROOT / "docs" / "API_ENDPOINTS_OVERVIEW.md"
    assert path.is_file()

    text = path.read_text(encoding="utf-8")
    for module in (
        "Health",
        "System",
        "Database",
        "Data Sources",
        "Quant",
        "LLM",
        "Screening",
        "Committee",
        "Order Price",
        "Virtual Trading",
        "Alerts",
        "Recheck",
        "Review",
        "Memory",
        "Config",
    ):
        assert module in text

    assert "Real trading involved" in text
    assert "No" in text
