import builtins

from scripts.run_real_quant_top500 import run_real_quant_top500


def test_real_quant_script_does_not_import_llm_gateway(monkeypatch, tmp_path) -> None:
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("llm_gateway"):
            raise AssertionError("real quant script must not import llm_gateway")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    report = run_real_quant_top500(
        provider="mock",
        history_provider="mock",
        backup_history_provider=None,
        top_n=5,
        sample_limit=5,
        output=tmp_path / "no_llm_report.json",
        progress=False,
    )

    assert report["no_llm_call_verified"] is True
