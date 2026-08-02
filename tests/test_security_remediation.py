from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
import yaml
from pydantic import ValidationError

from backend.api.internal_auth import PasswordChangeRequest, PasswordResetRequest
from scripts import audit_workspace_layout
from scripts import check_workspace_secret_exposure as secret_scan


ROOT = Path(__file__).resolve().parents[1]


def test_password_minimum_is_16() -> None:
    with pytest.raises(ValidationError):
        PasswordResetRequest(temporary_password="x" * 15)
    with pytest.raises(ValidationError):
        PasswordChangeRequest(current_password="old", new_password="x" * 15)
    assert PasswordResetRequest(temporary_password="x" * 16)


def test_shared_identity_is_labeled_in_api_and_ui() -> None:
    api = (ROOT / "backend/api/internal_auth.py").read_text(encoding="utf-8")
    ui = (ROOT / "frontend/src/layouts/MainLayout.vue").read_text(encoding="utf-8")
    assert '"SHARED_IDENTITY"' in api
    assert "不能进行个人追责" in ui


def test_cloudflare_identity_uses_verified_email() -> None:
    verifier = (ROOT / "backend/core/internal_auth.py").read_text(encoding="utf-8")
    middleware = (ROOT / "backend/core/internal_middleware.py").read_text(encoding="utf-8")
    assert 'options={"require": ["exp", "iss", "aud", "email"]}' in verifier
    assert "request.state.access_email = user.email" in middleware


def test_renderer_cannot_read_session_token() -> None:
    preload = (ROOT / "frontend/electron/preload.ts").read_text(encoding="utf-8")
    types = (ROOT / "frontend/src/vite-env.d.ts").read_text(encoding="utf-8")
    renderer = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "frontend/src").rglob("*.ts"))
    assert "sessionToken" not in preload
    assert "sessionToken" not in types
    assert "getConnection" not in renderer


def test_packaged_mode_does_not_load_project_env() -> None:
    path_manager = (ROOT / "frontend/electron/pathManager.ts").read_text(encoding="utf-8")
    backend = (ROOT / "frontend/electron/backendManager.ts").read_text(encoding="utf-8")
    assert 'PYTHON_DOTENV_DISABLED: allowLegacySecretFallback ? "0" : "1"' in path_manager
    assert "!app.isPackaged" in backend
    assert "AI_TRADER_ALLOW_LEGACY_ENV_SECRET_FALLBACK" in backend


def test_secret_status_does_not_expose_sensitive_metadata() -> None:
    source = (ROOT / "frontend/electron/secretManager.ts").read_text(encoding="utf-8")
    status_block = source[source.index("type SecretStatus"):source.index("export class SecretManager")]
    assert "length" not in status_block.lower()
    assert "hash" not in status_block.lower()
    assert "prefix" not in status_block.lower()
    assert "ciphertext" not in status_block.lower()


def test_disabled_provider_secret_not_loaded_by_default() -> None:
    source = (ROOT / "frontend/electron/backendManager.ts").read_text(encoding="utf-8")
    assert 'AI_TRADER_DESKTOP_ENABLED_PROVIDERS || ""' in source
    assert "this.secrets.decrypted(enabled)" in source


def test_workspace_secret_scanner_detects_without_echo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = "TEST_ONLY_SECRET_VALUE_123"
    (tmp_path / ".env").write_text(f"API_TOKEN={fake}\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/leak.txt").write_text(f"accidental={fake}\n", encoding="utf-8")
    monkeypatch.setattr(secret_scan, "ROOT", tmp_path)
    monkeypatch.setattr(secret_scan, "SCAN_ROOTS", ("src",))
    report, code = secret_scan.scan(tmp_path / "report.json")
    serialized = json.dumps(report)
    assert code == 1
    assert report["status"] == "EXPOSURE_DETECTED"
    assert fake not in serialized
    assert report["findings"][0]["variable"] == "API_TOKEN"


def test_permission_denied_not_reported_as_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("API_TOKEN=TEST_ONLY_SECRET\n", encoding="utf-8")

    def denied(permission_denied):
        permission_denied.append({"path": "backups/protected", "operation": "traverse"})
        return iter(())

    monkeypatch.setattr(secret_scan, "ROOT", tmp_path)
    monkeypatch.setattr(secret_scan, "candidate_files", denied)
    report, code = secret_scan.scan(tmp_path / "report.json")
    assert code == 2
    assert report["status"] == "INCOMPLETE"


def test_script_classification_covers_tracked_scripts() -> None:
    manifest = yaml.safe_load((ROOT / "scripts/SCRIPT_CLASSIFICATION.yaml").read_text(encoding="utf-8"))
    classified = {item["script"] for item in manifest["scripts"]}
    tracked = set(subprocess.check_output(
        ["git", "ls-files", "scripts"], cwd=ROOT, text=True, encoding="utf-8"
    ).splitlines())
    tracked_scripts = {path for path in tracked if Path(path).suffix.lower() in {".py", ".ps1", ".bat", ".cmd"}}
    assert tracked_scripts <= classified
    assert {
        "scripts/audit_workspace_layout.py",
        "scripts/check_workspace_secret_exposure.py",
        "scripts/run_quality_gate.py",
        "scripts/scan_desktop_security_build.py",
        "scripts/finalize_security_remediation_reports.py",
    } <= classified
    assert manifest["recommended_daily_entry"] == "scripts/run_v2_postclose_official_once.py"
    assert sum(item["safe_for_daily_use"] for item in manifest["scripts"]) == 1


def test_temp_and_tmp_are_both_governed() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "tmp/" in ignore
    assert "temp/" in ignore
    assert (ROOT / "docs/ARTIFACT_AND_RETENTION_POLICY.md").exists()


def test_workspace_audit_is_read_only() -> None:
    source = Path(audit_workspace_layout.__file__).read_text(encoding="utf-8")
    forbidden = ("unlink(", "rmtree(", "remove(", "git gc", "git clean")
    assert not any(item in source for item in forbidden)


def test_security_business_defaults_remain_fail_closed() -> None:
    system = yaml.safe_load((ROOT / "config/system.yaml").read_text(encoding="utf-8"))
    schedule = yaml.safe_load((ROOT / "config/schedule.yaml").read_text(encoding="utf-8"))
    paper = yaml.safe_load((ROOT / "config/paper_trading.yaml").read_text(encoding="utf-8"))
    virtual = yaml.safe_load((ROOT / "config/virtual_trading.yaml").read_text(encoding="utf-8"))
    assert system["safety"]["enable_real_trading"] is False
    assert system["runtime"]["enable_scheduler"] is False
    assert all(not section["enabled"] for section in schedule["schedule"].values())
    assert paper["paper_trading"]["enabled"] is False
    assert virtual["virtual_trading"]["enabled"] is False


def test_quality_gate_disables_external_side_effects() -> None:
    source = (ROOT / "scripts/run_quality_gate.py").read_text(encoding="utf-8")
    for marker in (
        '"ENABLE_REAL_TRADING": "false"',
        '"SCHEDULER_ENABLED": "false"',
        '"LLM_REAL_CALLS_ENABLED": "false"',
        '"AI_TRADER_DISABLE_EXTERNAL_PROVIDERS": "true"',
    ):
        assert marker in source
