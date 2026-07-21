$Service = Get-Service cloudflared -ErrorAction SilentlyContinue
$Process = Get-Process cloudflared -ErrorAction SilentlyContinue
[pscustomobject]@{
  service_status = if ($Service) { [string]$Service.Status } else { "NOT_INSTALLED" }
  process_count = @($Process).Count
  token_file_configured = Test-Path "C:\ProgramData\AITraderAssistant\secrets\cloudflared-token.txt"
  inbound_firewall_rule_created_by_project = $false
} | ConvertTo-Json -Depth 3
