param(
    [Parameter(Mandatory=$true)][string]$PublicHostname,
    [ValidateSet('LOCAL_SHARED_PASSWORD','CLOUDFLARE_ACCESS','CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD')][string]$AuthMode = 'LOCAL_SHARED_PASSWORD',
    [string]$SharedUsername = 'partners',
    [string]$TeamDomain = '',
    [string]$AccessAud = '',
    [string]$AllowedUserEmails = '',
    [string]$InternalUserRoles = '{}',
    [switch]$ReuseExistingSharedPassword
)
$ErrorActionPreference = "Stop"
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "ADMINISTRATOR_REQUIRED" }
if ($PublicHostname -notmatch '^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?){2,}$') { throw "PUBLIC_HOSTNAME_MUST_BE_NON_ROOT_SUBDOMAIN" }
$Emails = @()
$Roles = @{}
if ($AuthMode -eq 'LOCAL_SHARED_PASSWORD') {
    if ($SharedUsername -notmatch '^[A-Za-z0-9_.-]{3,64}$') { throw "SHARED_LOGIN_USERNAME_INVALID" }
} else {
    if ($TeamDomain -notmatch '^[a-z0-9-]+\.cloudflareaccess\.com$') { throw "CLOUDFLARE_TEAM_DOMAIN_INVALID" }
    if ($AccessAud -notmatch '^[A-Za-z0-9_-]{8,128}$') { throw "CLOUDFLARE_ACCESS_AUD_INVALID" }
    $Emails = @($AllowedUserEmails.Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ } | Sort-Object -Unique)
    if ($Emails.Count -ne 4) { throw "EXACTLY_FOUR_ALLOWED_EMAILS_REQUIRED" }
    foreach ($Email in $Emails) { try { $Parsed = [System.Net.Mail.MailAddress]::new($Email); if ($Parsed.Address -ne $Email) { throw "invalid" } } catch { throw "INVALID_ALLOWED_EMAIL" } }
    try { $ParsedRoles = $InternalUserRoles | ConvertFrom-Json } catch { throw "INTERNAL_USER_ROLES_INVALID_JSON" }
    foreach ($Property in $ParsedRoles.PSObject.Properties) { $Roles[$Property.Name.ToLowerInvariant()] = ([string]$Property.Value).ToUpperInvariant() }
    if ($Roles.Count -ne 4) { throw "EXACTLY_FOUR_INTERNAL_ROLES_REQUIRED" }
    foreach ($Email in $Emails) { if (-not $Roles.ContainsKey($Email) -or @('ADMIN','TRADER','VIEWER') -notcontains $Roles[$Email]) { throw "INTERNAL_USER_ROLE_MAPPING_INVALID" } }
    if (@($Roles.Values | Where-Object { $_ -eq 'ADMIN' }).Count -lt 1) { throw "AT_LEAST_ONE_ADMIN_REQUIRED" }
}

$ProgramDataRoot = Join-Path $env:ProgramData "AITraderAssistant"
$InstallRoot = Join-Path $env:ProgramFiles "AITraderAssistant\InternalWeb"
$Release = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "release\internal-web"
if (-not (Test-Path (Join-Path $Release "ai-trader-internal-web.exe")) -or -not (Test-Path (Join-Path $Release "AITraderInternalService\AITraderInternalService.exe"))) { throw "INTERNAL_WEB_RELEASE_NOT_BUILT" }
foreach($name in @("data","cache","outputs","logs","backups","config","secrets","diagnostics","temp")){ New-Item -ItemType Directory -Force -Path (Join-Path $ProgramDataRoot $name) | Out-Null }
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
if (Get-Service -Name "AITraderInternalWeb" -ErrorAction SilentlyContinue) {
    Stop-Service -Name "AITraderInternalWeb" -Force -ErrorAction SilentlyContinue
    & sc.exe delete AITraderInternalWeb | Out-Null
    Start-Sleep -Seconds 2
}
Copy-Item -Recurse -Force (Join-Path $Release "*") $InstallRoot
if (Test-Path (Join-Path $InstallRoot "config")) {
    Get-ChildItem (Join-Path $InstallRoot "config") -File | ForEach-Object {
        $target = Join-Path (Join-Path $ProgramDataRoot "config") $_.Name
        if (-not (Test-Path $target)) { Copy-Item $_.FullName $target }
    }
}
$EnvFile = Join-Path $ProgramDataRoot "config\internal-web.env"
if (Test-Path $EnvFile) {
    $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Copy-Item $EnvFile (Join-Path $ProgramDataRoot "backups\internal-web.env.$Stamp.bak")
}
@"
APP_ENV=production
APP_RUNTIME_MODE=INTERNAL_WEB_SERVER
APP_PUBLIC_HOSTNAME=$PublicHostname
APP_ORIGIN_HOST=127.0.0.1
APP_ORIGIN_PORT=8080
AUTH_MODE=$AuthMode
SHARED_LOGIN_USERNAME=$SharedUsername
CLOUDFLARE_TEAM_DOMAIN=$TeamDomain
CLOUDFLARE_ACCESS_AUD=$AccessAud
ALLOWED_USER_EMAILS=$($Emails -join ',')
INTERNAL_USER_ROLES=$($Roles | ConvertTo-Json -Compress)
CLOUDFLARE_ACCESS_SESSION_HOURS=12
ALLOW_LOCAL_AUTH_BYPASS=false
ENABLE_REAL_TRADING=false
AI_AUTO_REAL_ORDER_ENABLED=false
SCHEDULER_ENABLED=false
AI_TRADER_CONFIG_DIR=$ProgramDataRoot\config
AI_TRADER_SERVER_DATA_ROOT=$ProgramDataRoot
AI_TRADER_LOG_DIR=$ProgramDataRoot\logs
"@ | Set-Content -LiteralPath $EnvFile -Encoding ascii
icacls $ProgramDataRoot /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" | Out-Null
$WebExe = Join-Path $InstallRoot "ai-trader-internal-web.exe"
if ($AuthMode -eq 'LOCAL_SHARED_PASSWORD') {
    $DatabasePath = Join-Path $ProgramDataRoot "data\ai_trader_internal.db"
    if ($ReuseExistingSharedPassword) {
        if (-not (Test-Path $DatabasePath)) { throw "EXISTING_SHARED_PASSWORD_DATABASE_NOT_FOUND" }
    } else {
        & $WebExe set-shared-password
        if ($LASTEXITCODE -ne 0) { throw "SHARED_PASSWORD_SETUP_FAILED" }
    }
}
$ServiceExe = Join-Path $InstallRoot "AITraderInternalService\AITraderInternalService.exe"
& $ServiceExe --startup auto install
if ($LASTEXITCODE -ne 0) { throw "INTERNAL_WEB_SERVICE_INSTALL_FAILED" }
& sc.exe failure AITraderInternalWeb reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
& sc.exe failureflag AITraderInternalWeb 1 | Out-Null
& $ServiceExe start
$HealthDeadline = (Get-Date).AddSeconds(90)
do {
    Start-Sleep -Seconds 2
    $Listener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
    $Service = Get-Service -Name "AITraderInternalWeb" -ErrorAction SilentlyContinue
    if ($Listener -and $Service.Status -eq 'Running') { break }
} while ((Get-Date) -lt $HealthDeadline)
if (-not $Listener -or @($Listener | Where-Object { $_.LocalAddress -notin @('127.0.0.1','::1') }).Count -gt 0) { throw "INTERNAL_WEB_LOOPBACK_HEALTH_CHECK_FAILED" }
Write-Host "AI Trader Internal Web installed and listening only on loopback port 8080."
