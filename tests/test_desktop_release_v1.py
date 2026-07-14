from __future__ import annotations

import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from backend.core.desktop import DesktopPaths, prepare_desktop_database
from backend.main import create_app
from scripts.scan_v1_release import scan


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_paths_and_database_are_created_under_user_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_TRADER_USER_DATA_DIR", str(tmp_path / "中文用户目录"))
    monkeypatch.delenv("AI_TRADER_DB_PATH", raising=False)
    paths = DesktopPaths.from_environment()
    result = prepare_desktop_database(paths)
    assert paths.database == paths.root / "data" / "ai_trader.db"
    assert paths.database.is_file()
    assert result["integrity"] == "ok"
    second = prepare_desktop_database(paths)
    assert second["backup_created"] is True
    assert list(paths.backups.glob("ai_trader_*.db"))


def test_desktop_local_api_requires_ephemeral_token(monkeypatch) -> None:
    monkeypatch.setenv("AI_TRADER_DESKTOP_MODE", "true")
    monkeypatch.setenv("AI_TRADER_LOCAL_API_TOKEN", "test-session-token-with-at-least-32-characters")
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    assert client.get("/api/version").status_code == 401
    response = client.get(
        "/api/version",
        headers={"X-AI-Trader-Token": "test-session-token-with-at-least-32-characters"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["app_version"] == "1.0.0"


def test_release_versions_and_embedded_runtime_configs_are_consistent() -> None:
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    spec = (ROOT / "build" / "pyinstaller" / "ai_trader_backend.spec").read_text(encoding="utf-8")
    builder = (ROOT / "frontend" / "electron-builder.config.js").read_text(encoding="utf-8")
    assert package["version"] == "1.0.0"
    assert 'version = "1.0.0"' in pyproject
    for module in ("backend", "database", "datasource", "quant", "research", "fundamentals", "llm_gateway", "order_price", "position_sizing", "review", "trader_demo"):
        assert f'"{module}"' in spec
    assert 'name="ai_trader_backend"' in spec
    assert '"nsis"' in builder and 'target: "dir"' in builder
    assert ".env" not in spec and ".env" not in builder


def test_electron_secret_and_backend_lifecycle_are_main_process_only() -> None:
    secret_manager = (ROOT / "frontend" / "electron" / "secretManager.ts").read_text(encoding="utf-8")
    backend_manager = (ROOT / "frontend" / "electron" / "backendManager.ts").read_text(encoding="utf-8")
    preload = (ROOT / "frontend" / "electron" / "preload.ts").read_text(encoding="utf-8")
    settings_view = (ROOT / "frontend" / "src" / "views" / "SettingsView.vue").read_text(encoding="utf-8")
    first_run_view = (ROOT / "frontend" / "src" / "views" / "FirstRunView.vue").read_text(encoding="utf-8")
    assert "safeStorage.encryptString" in secret_manager
    assert "secrets.enc.json" in secret_manager
    assert "windowsHide: true" in backend_manager
    assert "randomBytes(32)" in backend_manager
    assert "AI_TRADER_LOCAL_API_TOKEN" not in preload
    assert "localStorage" not in settings_view
    assert "localStorage" not in first_run_view


def test_release_scanner_rejects_known_secret_and_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "release-test-secret-value")
    (tmp_path / "payload.txt").write_text("release-test-secret-value", encoding="utf-8")
    (tmp_path / ".env").write_text("ENABLE_REAL_TRADING=false", encoding="utf-8")
    report = scan(tmp_path)
    assert report["passed"] is False
    assert report["known_secret_file_hits"] == ["payload.txt"]
    assert report["forbidden_credential_files"] == [".env"]


def test_release_scanner_inspects_portable_zip_contents(tmp_path: Path) -> None:
    archive = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("resources/config.txt", "ghp_abcdefghijklmnopqrstuvwxyz123456")
    report = scan(tmp_path)
    assert report["passed"] is False
    assert report["known_secret_file_hits"] == ["portable.zip!/resources/config.txt"]


def test_first_run_copies_seed_only_when_database_is_absent() -> None:
    source = (ROOT / "frontend" / "electron" / "firstRunManager.ts").read_text(encoding="utf-8")
    assert "if (existingDatabase)" in source
    assert "copyFile(databaseSource, paths.database)" in source
    assert "SEED_DATABASE_CHECKSUM_MISMATCH" in source
    assert "if (!(await exists(destination)))" in source
