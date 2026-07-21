param(
    [string]$Version = "1.0.0",
    [ValidateSet("x64")][string]$Architecture = "x64",
    [switch]$SkipSigning,
    [switch]$AllowDirty,
    [switch]$IncludeSeedData = $true,
    [string]$CondaEnvironment = "ai-stock-agent",
    [string]$OutputDirectory = "release/v1.0.0"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CondaInfo = conda env list --json | ConvertFrom-Json
$CondaPrefix = $CondaInfo.envs | Where-Object { (Split-Path $_ -Leaf) -eq $CondaEnvironment } | Select-Object -First 1
if (-not $CondaPrefix) { throw "Conda environment not found: $CondaEnvironment" }
$Python = Join-Path $CondaPrefix "python.exe"
$Frontend = Join-Path $Root "frontend"
$Output = Join-Path $Root $OutputDirectory
$Build = Join-Path $Root "build"

function Invoke-Checked([string]$Name, [scriptblock]$Action) {
    Write-Host "`n== $Name ==" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

function Invoke-PackagedSmoke([string]$Executable, [string]$UserData) {
    New-Item -ItemType Directory -Force -Path $UserData | Out-Null
    $previous = $env:AI_TRADER_SMOKE_TEST
    $previousRunAsNode = $env:ELECTRON_RUN_AS_NODE
    $env:AI_TRADER_SMOKE_TEST = "true"
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    try {
        $process = Start-Process -FilePath $Executable -ArgumentList "--user-data-dir=$UserData" -PassThru -WindowStyle Hidden
        if (-not $process.WaitForExit(120000)) { Stop-Process -Id $process.Id -Force; throw "Packaged smoke timeout" }
        if ($process.ExitCode -ne 0) { throw "Packaged smoke failed with exit code $($process.ExitCode)" }
    } finally {
        $env:AI_TRADER_SMOKE_TEST = $previous
        $env:ELECTRON_RUN_AS_NODE = $previousRunAsNode
    }
}

if (-not [Environment]::Is64BitOperatingSystem -or $env:OS -ne "Windows_NT") { throw "Windows x64 is required" }
if ($Version -ne "1.0.0") { throw "This release script is locked to version 1.0.0" }
if (-not (Select-String -Path (Join-Path $Root "pyproject.toml") -Quiet -Pattern 'version = "1.0.0"')) { throw "Python version mismatch" }
if (-not (Select-String -Path (Join-Path $Frontend "package.json") -Quiet -Pattern '"version": "1.0.0"')) { throw "Frontend version mismatch" }

$GitStatus = @(git -C $Root status --porcelain=v1)
$SourceDirty = $GitStatus.Count -gt 0
if ($SourceDirty -and -not $AllowDirty) {
    $GitStatus | ForEach-Object { Write-Host $_ }
    throw "Working tree is dirty. Review it and rerun with -AllowDirty when intentional."
}

Invoke-Checked "Python tests" { & $Python -m pytest }
Invoke-Checked "Security configuration" { & $Python scripts/check_security_config.py }
Invoke-Checked "Python compileall" { & $Python -m compileall -q backend database datasource quant research fundamentals llm_gateway market_review intraday_monitor order_price position_sizing review trader_demo }
Invoke-Checked "Frontend tests" { npm --prefix frontend test }
Invoke-Checked "Frontend typecheck" { npm --prefix frontend run typecheck }
Invoke-Checked "Frontend build" { npm --prefix frontend run build }

if ($IncludeSeedData) {
    Invoke-Checked "Seed bundle creation" { & $Python scripts/create_v1_seed_bundle.py }
    Invoke-Checked "Seed bundle verification" { & $Python scripts/verify_v1_seed_bundle.py }
} elseif (-not (Test-Path (Join-Path $Build "seed/SEED_DATA_MANIFEST_1.0.0.json"))) {
    throw "Seed bundle is required when -IncludeSeedData is disabled"
}

$BackendDist = Join-Path $Build "backend"
Remove-Item -LiteralPath $BackendDist -Recurse -Force -ErrorAction SilentlyContinue
Invoke-Checked "PyInstaller backend" {
    & $Python -m PyInstaller --noconfirm --clean --distpath $BackendDist --workpath (Join-Path $Build "pyinstaller/work") (Join-Path $Build "pyinstaller/ai_trader_backend.spec")
}
$BackendExe = Join-Path $BackendDist "ai-trader-backend/ai_trader_backend.exe"
Invoke-Checked "Backend executable smoke" {
    & $Python scripts/smoke_backend_executable.py $BackendExe --seed (Join-Path $Build "seed/data/ai_trader_seed.db") --config (Join-Path $Root "config")
}

$SigningConfigured = -not $SkipSigning -and -not [string]::IsNullOrWhiteSpace($env:CSC_LINK)
if (-not $SigningConfigured) { $env:CSC_IDENTITY_AUTO_DISCOVERY = "false" }
Remove-Item -LiteralPath (Join-Path $Build "electron") -Recurse -Force -ErrorAction SilentlyContinue
Invoke-Checked "Electron Builder" { npm --prefix frontend run package:win }

$ElectronBuild = Join-Path $Build "electron"
$GeneratedSetup = Get-ChildItem -LiteralPath $ElectronBuild -Filter "AI-Trader-Assistant-Setup-$Version-x64.exe" -File | Select-Object -First 1
if (-not $GeneratedSetup) { throw "NSIS installer not found" }
$Unpacked = Join-Path $ElectronBuild "win-unpacked"
$ResourceBackend = Join-Path $Unpacked "resources/backend/ai_trader_backend.exe"
if (-not (Test-Path $ResourceBackend)) { throw "Packaged backend resource is missing" }
if (-not (Test-Path (Join-Path $Unpacked "resources/seed/data/ai_trader_seed.db"))) { throw "Packaged seed database is missing" }

Remove-Item -LiteralPath $Output -Recurse -Force -ErrorAction SilentlyContinue
$Directories = "installer", "portable", "checksums", "manifests", "docs", "reports"
$Directories | ForEach-Object { New-Item -ItemType Directory -Force -Path (Join-Path $Output $_) | Out-Null }
$Unsigned = if ($SigningConfigured) { "" } else { "-unsigned" }
$SetupName = "AI-Trader-Assistant-Setup-$Version-$Architecture$Unsigned.exe"
$PortableName = "AI-Trader-Assistant-Portable-$Version-$Architecture$Unsigned.zip"
Copy-Item -LiteralPath $GeneratedSetup.FullName -Destination (Join-Path $Output "installer/$SetupName")
Compress-Archive -Path (Join-Path $Unpacked "*") -DestinationPath (Join-Path $Output "portable/$PortableName") -CompressionLevel Optimal

$PortableSmoke = Join-Path $env:TEMP "ai-trader-portable-smoke-v100"
Remove-Item -LiteralPath $PortableSmoke -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -LiteralPath (Join-Path $Output "portable/$PortableName") -DestinationPath $PortableSmoke
Invoke-Checked "Portable smoke" { Invoke-PackagedSmoke (Join-Path $PortableSmoke "AI Trader Assistant.exe") (Join-Path $env:TEMP "ai-trader-portable-smoke-user-v100") }

$InstallRoot = Join-Path $env:TEMP ("ai-trader-install-" + [guid]::NewGuid().ToString("N"))
Invoke-Checked "Installer install" {
    $installer = Start-Process -FilePath (Join-Path $Output "installer/$SetupName") -ArgumentList "/S", "/D=$InstallRoot" -PassThru -Wait
    if ($installer.ExitCode -ne 0) { throw "Installer failed with exit code $($installer.ExitCode)" }
}
Invoke-Checked "Installed application smoke" { Invoke-PackagedSmoke (Join-Path $InstallRoot "AI Trader Assistant.exe") (Join-Path $env:TEMP "ai-trader-installer-smoke-user-v100") }
$Uninstaller = Join-Path $InstallRoot "Uninstall AI Trader Assistant.exe"
if (Test-Path $Uninstaller) { Start-Process -FilePath $Uninstaller -ArgumentList "/S" -Wait | Out-Null }

Copy-Item -LiteralPath (Join-Path $Build "seed/SEED_DATA_MANIFEST_1.0.0.json") -Destination (Join-Path $Output "manifests/SEED_DATA_MANIFEST_1.0.0.json")
foreach ($doc in "USER_MANUAL_V1.md", "INSTALLATION_V1.md", "TROUBLESHOOTING_V1.md") {
    Copy-Item -LiteralPath (Join-Path $Root "docs/$doc") -Destination (Join-Path $Output "docs/$doc")
}

$SigningStatus = if ($SigningConfigured) { "CONFIGURED" } else { "NOT_CONFIGURED" }
Invoke-Checked "Unpacked application secret and path scan" { & $Python scripts/scan_v1_release.py $Unpacked --output (Join-Path $Build "electron/unpacked_security_scan.json") }
Invoke-Checked "Release metadata" { & $Python scripts/generate_v1_release_metadata.py --root $Root --output $Output --code-signing $SigningStatus }
Invoke-Checked "Release secret and path scan" { & $Python scripts/scan_v1_release.py $Output --output (Join-Path $Output "reports/release_security_scan.json") }
Invoke-Checked "Release checksums" { & $Python scripts/generate_v1_release_metadata.py --output $Output --checksums-only }

Write-Host "`nRelease candidate created: $Output" -ForegroundColor Green
Write-Host "Setup: $SetupName"
Write-Host "Portable: $PortableName"





