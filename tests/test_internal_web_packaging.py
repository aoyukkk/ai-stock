from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_internal_web_packaging_and_service_scripts_are_present_and_safe():
    required = [
        "packaging/pyinstaller_internal_web.spec",
        "scripts/build_internal_web_release.ps1",
        "scripts/install_internal_web_service.ps1",
        "scripts/uninstall_internal_web_service.ps1",
        "scripts/status_internal_web_service.ps1",
        "scripts/install_cloudflared_service.ps1",
        "scripts/uninstall_cloudflared_service.ps1",
        "scripts/status_cloudflared_service.ps1",
        "docs/CLOUDFLARE_INTERNAL_DEPLOYMENT.md",
    ]
    assert all((ROOT / item).is_file() for item in required)
    install = (ROOT / "scripts/install_internal_web_service.ps1").read_text(encoding="utf-8")
    assert "APP_ORIGIN_HOST=127.0.0.1" in install
    assert "ENABLE_REAL_TRADING=false" in install
    assert "SCHEDULER_ENABLED=false" in install
    assert "EXACTLY_FOUR_ALLOWED_EMAILS_REQUIRED" in install
    assert "AUTH_MODE=$AuthMode" in install
    assert "ai-trader-internal-web.exe" in install
    assert "set-shared-password" in install
    assert "ReuseExistingSharedPassword" in install
    assert "AITraderInternalService\\AITraderInternalService.exe" in install
    assert "0.0.0.0" not in install
    assert "New-NetFirewallRule" not in install
    assert "AI_TRADER_LOG_DIR=" in install
    assert "sc.exe failure AITraderInternalWeb" in install
    build = (ROOT / "scripts/build_internal_web_release.ps1").read_text(encoding="utf-8")
    assert "FRONTEND_DEPENDENCY_INSTALL_FAILED" in build
    assert "FRONTEND_BUILD_FAILED" in build
    service = (ROOT / "backend/internal_web_service.py").read_text(encoding="utf-8")
    assert "PrepareToHostSingle" in service
    assert "StartServiceCtrlDispatcher" in service


def test_cloudflared_service_uses_protected_token_file_not_token_value():
    install = (ROOT / "scripts/install_cloudflared_service.ps1").read_text(encoding="utf-8")
    assert "--token-file" in install
    assert "SYSTEM:F" in install and "Administrators:F" in install
    assert "--token " not in install
    assert "New-NetFirewallRule" not in install
    assert "sc.exe failure cloudflared" in install


def test_manual_guide_contains_no_real_deployment_credentials():
    guide = (ROOT / "docs/CLOUDFLARE_INTERNAL_DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "<temporary-api-token>" in guide
    assert "LOCAL_SHARED_PASSWORD" in guide
    assert "Argon2id" in guide
    assert "cfut_" not in guide
    assert "ghp_" not in guide
    assert "5768e9852cc17a0fd6d69be29110f046" not in guide
    assert "ecd8e712986706c9cbce24b9a685515c" not in guide
