param(
    [string]$CloudflaredExe = "C:\Program Files\cloudflared\cloudflared.exe",
    [string]$TokenFile = "C:\ProgramData\AITraderAssistant\secrets\cloudflared-token.txt",
    [string]$StagedTokenFile = "",
    [string]$StagedCloudflaredExe = ""
)
$ErrorActionPreference = "Stop"
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "ADMINISTRATOR_REQUIRED" }
if ($StagedCloudflaredExe) {
    if (-not (Test-Path -LiteralPath $StagedCloudflaredExe)) { throw "STAGED_CLOUDFLARED_EXECUTABLE_NOT_FOUND" }
    $Signature = Get-AuthenticodeSignature -LiteralPath $StagedCloudflaredExe
    if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Cloudflare') { throw "CLOUDFLARED_SIGNATURE_INVALID" }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $CloudflaredExe) | Out-Null
    Move-Item -LiteralPath $StagedCloudflaredExe -Destination $CloudflaredExe -Force
}
if (-not (Test-Path -LiteralPath $CloudflaredExe)) { throw "CLOUDFLARED_EXECUTABLE_NOT_FOUND" }
if ($StagedTokenFile) {
    if (-not (Test-Path -LiteralPath $StagedTokenFile)) { throw "STAGED_CLOUDFLARED_TOKEN_FILE_NOT_FOUND" }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $TokenFile) | Out-Null
    Move-Item -LiteralPath $StagedTokenFile -Destination $TokenFile -Force
}
if (-not (Test-Path -LiteralPath $TokenFile)) { throw "CLOUDFLARED_TOKEN_FILE_NOT_FOUND" }
$Version = & $CloudflaredExe --version
if ($LASTEXITCODE -ne 0) { throw "CLOUDFLARED_VERSION_CHECK_FAILED" }
icacls $TokenFile /inheritance:r /grant:r "Administrators:F" "SYSTEM:F" | Out-Null
if (Get-Service cloudflared -ErrorAction SilentlyContinue) { Stop-Service cloudflared -Force; & sc.exe delete cloudflared | Out-Null; Start-Sleep -Seconds 2 }
$BinPath = '"' + $CloudflaredExe + '" tunnel --no-autoupdate --loglevel info run --token-file "' + $TokenFile + '"'
try {
    New-Service -Name cloudflared -BinaryPathName $BinPath -StartupType Automatic -DisplayName "Cloudflare Tunnel" | Out-Null
} catch {
    throw "CLOUDFLARED_SERVICE_INSTALL_FAILED:$($_.Exception.Message)"
}
& sc.exe description cloudflared "Outbound-only Cloudflare Tunnel for AI Trader Internal" | Out-Null
& sc.exe failure cloudflared reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
& sc.exe failureflag cloudflared 1 | Out-Null
Start-Service cloudflared
Start-Sleep -Seconds 5
if ((Get-Service cloudflared).Status -ne 'Running') { throw "CLOUDFLARED_SERVICE_NOT_RUNNING" }
Write-Host "cloudflared service installed. Tunnel token was read from a protected file and was not placed on the command line."
