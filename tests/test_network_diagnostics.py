import os

from scripts import network_diagnostics


def test_network_diagnostics_importable() -> None:
    assert network_diagnostics.REPORT_PATH.name == "network_diagnostics_report.json"


def test_redacts_proxy_urls() -> None:
    redacted = network_diagnostics.redact_proxy_url("http://user:secret@127.0.0.1:7890")

    assert "secret" not in redacted
    assert "<redacted>" in redacted
    assert "127.0.0.1:7890" in redacted


def test_no_proxy_does_not_permanently_modify_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@127.0.0.1:7890")
    monkeypatch.setattr(network_diagnostics, "TARGET_URLS", ())

    report = network_diagnostics.run_network_diagnostics(
        no_proxy=True,
        output=tmp_path / "network.json",
    )

    assert report["no_proxy"] is True
    assert os.environ["HTTPS_PROXY"] == "http://user:secret@127.0.0.1:7890"
