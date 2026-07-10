from scripts import smoke_real_llm_gateway


def test_real_llm_smoke_is_disabled_by_default(monkeypatch, capsys) -> None:
    monkeypatch.delenv("RUN_REAL_LLM_SMOKE", raising=False)
    assert smoke_real_llm_gateway.main() == 0
    assert '"executed": false' in capsys.readouterr().out.lower()
