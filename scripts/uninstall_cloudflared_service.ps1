param([switch]$DeleteTokenFile)
$ErrorActionPreference = "Stop"
if (Get-Service cloudflared -ErrorAction SilentlyContinue) {
    Stop-Service cloudflared -Force
    & sc.exe delete cloudflared | Out-Null
}
if ($DeleteTokenFile) { Remove-Item -LiteralPath "C:\ProgramData\AITraderAssistant\secrets\cloudflared-token.txt" -Force -ErrorAction SilentlyContinue }
Write-Host "cloudflared service removed. Cloudflare dashboard resources were not changed."
