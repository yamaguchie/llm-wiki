<#
  Bunkyo disability-welfare Agentic Search chat - START server
  Document root = repo root (llm_wiki/) so pages/ and entities/ links resolve.
  Usage:
    powershell -ExecutionPolicy Bypass -File graphrag\serve.ps1          # start
    powershell -ExecutionPolicy Bypass -File graphrag\serve.ps1 -Open    # start + open browser
    powershell -ExecutionPolicy Bypass -File graphrag\serve.ps1 -Port 9000
#>
param(
  [int]$Port = 8788,
  [switch]$Open
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent          # parent of graphrag/ = llm_wiki/
$pidFile = Join-Path $PSScriptRoot ".server.pid"
$url = "http://127.0.0.1:$Port/graphrag/chat/index.html"

# --- stop any existing server on this port (clean re-run) ---
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { ($_.CommandLine -like '*serve_md.py*' -or $_.CommandLine -like '*http.server*') -and $_.CommandLine -like "*$Port*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 400

# --- detect Python launcher: py -3.14 -> py -> python ---
$launcher = $null; $pre = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3.14 --version *> $null 2>&1
  if ($LASTEXITCODE -eq 0) { $launcher = 'py'; $pre = @('-3.14') } else { $launcher = 'py' }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  $launcher = 'python'
} else {
  Write-Error 'Python not found. Please install Python 3.'; exit 1
}
$server = Join-Path $PSScriptRoot 'serve_md.py'
$allArgs = $pre + @($server,"$Port",$root)

# --- start (hidden background window) ---
$proc = Start-Process -FilePath $launcher -ArgumentList $allArgs -WorkingDirectory $root -WindowStyle Hidden -PassThru
$proc.Id | Out-File -FilePath $pidFile -Encoding ascii

# --- readiness check (up to ~8s) ---
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
  try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $ok = $true; break }
  } catch { Start-Sleep -Milliseconds 400 }
}

if ($ok) {
  Write-Host ""
  Write-Host "  Server started (PID $($proc.Id), port $Port)" -ForegroundColor Green
  Write-Host "  Doc root: $root"
  Write-Host ""
  Write-Host "  Open the chat:" -ForegroundColor Cyan
  Write-Host "    $url"
  Write-Host ""
  Write-Host "  Stop: powershell -ExecutionPolicy Bypass -File graphrag\stop.ps1   (or double-click stop.cmd)"
  if ($Open) { Start-Process $url }
} else {
  Write-Host "  Readiness check failed. Port $Port may be in use." -ForegroundColor Yellow
  Write-Host "  Retry on another port: powershell -ExecutionPolicy Bypass -File graphrag\serve.ps1 -Port 9001"
}
