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
    & $Python -m PyInstaller --noconfirm --clean --distpath (Join-Path $Root "release\internal-web") --workpath (Join-Path $Root "build\internal-web") (Join-Path $Root "packaging\pyinstaller_internal_web.spec")
    if ($LASTEXITCODE -ne 0) { throw "PYINSTALLER_BUILD_FAILED" }
    Copy-Item -Recurse -Force (Join-Path $Root "config") (Join-Path $Root "release\internal-web\config")
    Write-Host "Internal web release built: release\internal-web"
} finally { Pop-Location }
