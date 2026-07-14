<#
  Bunkyo disability-welfare Agentic Search chat - STOP server
  Usage:
    powershell -ExecutionPolicy Bypass -File graphrag\stop.ps1           # stop default port 8788
    powershell -ExecutionPolicy Bypass -File graphrag\stop.ps1 -Port 9000
    powershell -ExecutionPolicy Bypass -File graphrag\stop.ps1 -All      # stop ALL http.server
#>
param(
  [int]$Port = 8788,
  [switch]$All
)

$pidFile = Join-Path $PSScriptRoot ".server.pid"
$killed = @()

$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object {
    ($_.CommandLine -like '*serve_md.py*' -or $_.CommandLine -like '*http.server*' -or $_.CommandLine -like '*agent\server.py*' -or $_.CommandLine -like '*agent/server.py*') -and ($All -or $_.CommandLine -like "*$Port*")
  }

foreach ($p in $procs) {
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  $killed += $p.ProcessId
}

if (Test-Path $pidFile) { Remove-Item $pidFile -ErrorAction SilentlyContinue }

if ($killed.Count -gt 0) {
  Write-Host "  Stopped: PID $($killed -join ', ')" -ForegroundColor Green
} else {
  Write-Host "  No matching http.server process found (already stopped)." -ForegroundColor Yellow
}
