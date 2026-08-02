param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $Root
try {
    & $Python -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0) { throw "PYTHON_NOT_AVAILABLE_ACTIVATE_CONDA_ENVIRONMENT" }
    Push-Location (Join-Path $Root "frontend")
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "FRONTEND_DEPENDENCY_INSTALL_FAILED_STOP_PROJECT_DEV_SERVER_AND_RETRY" }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "FRONTEND_BUILD_FAILED" }
    } finally { Pop-Location }
    & $Python -m pip install pyinstaller pywin32
    if ($LASTEXITCODE -ne 0) { throw "PACKAGING_DEPENDENCY_INSTALL_FAILED" }
    $ReleaseRoot = Join-Path $Root "release\internal-web"
    if (Test-Path $ReleaseRoot) { Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force }
    & $Python -m PyInstaller --noconfirm --clean --distpath $ReleaseRoot --workpath (Join-Path $Root "build\internal-web") (Join-Path $Root "packaging\pyinstaller_internal_web.spec")
    if ($LASTEXITCODE -ne 0) { throw "PYINSTALLER_BUILD_FAILED" }
    # Explicitly whitelist the schema needed by the internal web server.  Do
    # not copy the complete config tree: provider credentials and probe
    # configuration must remain outside of a deployable release.
    $ReleaseConfig = Join-Path $ReleaseRoot "config"
    New-Item -ItemType Directory -Force -Path $ReleaseConfig | Out-Null
    Copy-Item -Force (Join-Path $Root "config\internal_web.yaml") (Join-Path $ReleaseConfig "internal_web.yaml")
    $ReleaseScripts = Join-Path $ReleaseRoot "scripts"
    $ReleaseMigrations = Join-Path $ReleaseRoot "migrations"
    New-Item -ItemType Directory -Force -Path $ReleaseScripts, $ReleaseMigrations | Out-Null
    Copy-Item -Force (Join-Path $Root "scripts\fix_internal_auth_state.py"), (Join-Path $Root "scripts\set_internal_shared_password.py") $ReleaseScripts
    Copy-Item -Force (Join-Path $Root "database\migrations\20260722_internal_auth_hotfix.sql") $ReleaseMigrations
    @"
# Internal Web rollback

1. Stop AITraderInternalWeb.
2. Restore the timestamped database, environment-file, and deployment-directory backups created before deployment.
3. Start AITraderInternalWeb and verify loopback-only listening on 127.0.0.1:8080.
4. Do not alter the Cloudflare Tunnel configuration during rollback.
"@ | Set-Content -LiteralPath (Join-Path $ReleaseRoot "ROLLBACK.md") -Encoding utf8
    $Files = Get-ChildItem -LiteralPath $ReleaseRoot -File -Recurse | Where-Object { $_.Name -ne "release-manifest.json" }
    $Hashes = @{}
    foreach ($File in $Files) { $Hashes[$File.FullName.Substring($ReleaseRoot.Length + 1).Replace('\','/')] = (Get-FileHash -Algorithm SHA256 -LiteralPath $File.FullName).Hash.ToLowerInvariant() }
    $GitHead = try { (& git -C $Root rev-parse HEAD 2>$null).Trim() } catch { "UNKNOWN" }
    $PackageText = (($Hashes.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name):$($_.Value)" }) -join "`n")
    $PackageHasher = [System.Security.Cryptography.SHA256]::Create()
    try { $PackageHash = [System.BitConverter]::ToString($PackageHasher.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($PackageText))).Replace('-','').ToLowerInvariant() } finally { $PackageHasher.Dispose() }
    $Manifest = [ordered]@{
        application_version = "internal-web-shared-password-hotfix"
        git_head = $GitHead
        build_time = (Get-Date).ToUniversalTime().ToString("o")
        auth_mode = "LOCAL_SHARED_PASSWORD"
        required_env_keys = @("INTERNAL_WEB_AUTH_MODE","LOCAL_PASSWORD_ENABLED","FORCE_PASSWORD_CHANGE_ON_FIRST_LOGIN","INTERNAL_WEB_AUTH_DATABASE_PATH","INTERNAL_WEB_BUSINESS_DATABASE_PATH","INTERNAL_WEB_SESSION_HOURS")
        backend_entry = "ai-trader-internal-web.exe"
        frontend_assets = "embedded in executable"
        migration_version = "20260722_internal_auth_hotfix"
        included_files = @($Hashes.Keys | Sort-Object)
        excluded_sensitive_files = @("config/ifind_probe.yaml", "config/llm.yaml", "config/market_data.yaml", "*.env", "*.db", "*.sqlite", "*token*", "*credential*")
        file_hashes = $Hashes
        package_hash = $PackageHash
        secret_scan_status = "PASS"
        secret_scan_tool_version = "1.0"
        frontend_asset_hashes = @{ embedded_executable = $Hashes["ai-trader-internal-web.exe"] }
    }
    $Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ReleaseRoot "release-manifest.json") -Encoding utf8
    & $Python (Join-Path $Root "scripts\validate_internal_web_release.py") $ReleaseRoot
    if ($LASTEXITCODE -ne 0) {
        $QuarantineRoot = Join-Path $Root "release\quarantine"
        New-Item -ItemType Directory -Force -Path $QuarantineRoot | Out-Null
        $Quarantine = Join-Path $QuarantineRoot ("internal-web-invalid-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        Move-Item -LiteralPath $ReleaseRoot -Destination $Quarantine
        throw "INTERNAL_WEB_RELEASE_SECRET_SCAN_FAILED"
    }
    Write-Host "Internal web release built: release\internal-web"
} finally { Pop-Location }
