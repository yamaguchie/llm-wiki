<#
  Gemini-mode backend (agent/server.py): serves the chat + /api/ask (LLM planner/critic/answer + neural embeddings).
  Requires graphrag/.env with a valid GEMINI_API_KEY and google-genai installed under py -3.14.

  Pinned to `py -3.14` with a pre-flight import check: bare `python` here is a uv
  cpython-3.11 without google-genai, which fails with "No module named 'google.genai'"
  (incident 2026-07-15). Stop is by PORT + PROCESS TREE so nothing lingers.

  Usage:
    powershell -ExecutionPolicy Bypass -File graphrag\serve-gemini.ps1 -Open
    powershell -ExecutionPolicy Bypass -File graphrag\serve-gemini.ps1 -Port 8790
#>
param([int]$Port = 8790, [switch]$Open)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_ps_lib.ps1"

$root   = Split-Path $PSScriptRoot -Parent
$server = Join-Path $PSScriptRoot 'agent\server.py'
$url    = "http://127.0.0.1:$Port/"

# ── Resolve + pre-flight the ONLY sanctioned interpreter (py -3.14 with google-genai) ──
try {
  $py = Resolve-Py314 -RequireModules @('fastapi', 'uvicorn', 'google.genai')
} catch {
  Write-Host ""
  Write-Host "  [ERROR] $($_.Exception.Message)" -ForegroundColor Red
  Write-Host ""
  exit 1
}

# ── Stop any existing backend on this port (by port + process tree) ──
foreach ($id in (Get-PortListenerPids $Port)) { Stop-ProcessTree -RootPid ([int]$id) }
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'agent[\\/]+server\.py' -and $_.CommandLine -match "\b$Port\b" } |
  ForEach-Object { Stop-ProcessTree -RootPid ([int]$_.ProcessId) }
Start-Sleep -Milliseconds 400

$proc = Start-Process -FilePath $py -ArgumentList (@('-3.14', $server, "$Port", $root)) `
  -WorkingDirectory $root -WindowStyle Hidden -PassThru

$ok = Wait-HttpOk $url 20
if ($ok) {
  Write-Host ""
  Write-Host "  Gemini backend started (PID $($proc.Id), port $Port)" -ForegroundColor Green
  Write-Host "  Open the chat and turn ON the 'Geminiモード' checkbox:" -ForegroundColor Cyan
  Write-Host "    $url"
  Write-Host "  Stop: powershell -ExecutionPolicy Bypass -File graphrag\stop-all.ps1"
  if ($Open) { Start-Process $url }
} else {
  Write-Host "  Readiness check failed (port $Port in use?)." -ForegroundColor Yellow
}
