<#
  Stop BOTH servers of the GraphRAG demo (chat/Gemini backend on 8790 + review UI on 8789).

  Robust by design (see incident note in _ps_lib.ps1): kills by PORT and by PROCESS TREE,
  so a uvicorn reload worker (whose command line contains neither 'uvicorn' nor
  'review.main:app') cannot survive holding the port.

  Usage:
    powershell -ExecutionPolicy Bypass -File graphrag\stop-all.ps1
    powershell -ExecutionPolicy Bypass -File graphrag\stop-all.ps1 -ReviewPort 8789 -ChatPort 8790
#>
param(
  [int]$ReviewPort = 8789,
  [int]$ChatPort   = 8790
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_ps_lib.ps1"

# Also sweep the legacy static server (serve_md.py / http.server) if present.
$legacy = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'serve_md\.py' -or $_.CommandLine -match 'http\.server' }
foreach ($p in $legacy) { Stop-ProcessTree -RootPid ([int]$p.ProcessId) }

$killed = Stop-GraphRagServers -Ports @($ReviewPort, $ChatPort)

# Final confirmation that both ports are free.
$stillReview = Get-PortListenerPids $ReviewPort
$stillChat   = Get-PortListenerPids $ChatPort

if ($killed.Count -gt 0) {
  Write-Host "  Stopped: PID $($killed -join ', ')" -ForegroundColor Green
} else {
  Write-Host "  No GraphRAG server processes were running (already stopped)." -ForegroundColor Yellow
}
if ($stillReview) { Write-Host "  [WARN] Port $ReviewPort still has a listener (PID $($stillReview -join ', '))." -ForegroundColor Yellow }
if ($stillChat)   { Write-Host "  [WARN] Port $ChatPort still has a listener (PID $($stillChat -join ', '))." -ForegroundColor Yellow }
if (-not $stillReview -and -not $stillChat) {
  Write-Host "  Ports $ReviewPort and $ChatPort are free." -ForegroundColor Green
}
