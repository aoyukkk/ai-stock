$service = Get-Service -Name "AITraderInternalWeb" -ErrorAction SilentlyContinue
$listener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
[pscustomobject]@{
  service_status = if($service){$service.Status}else{"NOT_INSTALLED"}
  bound_addresses = @($listener | Select-Object -ExpandProperty LocalAddress -Unique)
  loopback_only = [bool]($listener) -and @($listener | Where-Object { $_.LocalAddress -notin @("127.0.0.1","::1") }).Count -eq 0
  scheduler_enabled = $false
  real_trading_enabled = $false
} | ConvertTo-Json -Depth 3
