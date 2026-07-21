$ErrorActionPreference = "Stop"
$ServiceExe = Join-Path $env:ProgramFiles "AITraderAssistant\InternalWeb\AITraderInternalService.exe"
if (Test-Path $ServiceExe) { & $ServiceExe stop; & $ServiceExe remove }
Write-Host "Service removed. ProgramData databases, outputs, backups and secrets were preserved."
