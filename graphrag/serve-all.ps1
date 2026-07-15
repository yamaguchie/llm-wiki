<#
  Start BOTH servers of the GraphRAG demo, correctly and safely.

  Lesson learned (why this script exists):
    - There are TWO servers:
        * Chat / Gemini backend : agent/server.py         on port 8790
        * Review UI (FastAPI)   : uvicorn review.main:app on port 8789
    - BOTH must run under `py -3.14`, because that is the interpreter that has
      fastapi + uvicorn + google-genai installed. Running the review UI under any
      other Python (e.g. bare `python`, which here is a uv cpython-3.11 without
      google-genai) makes LLM features fail with "No module named 'google.genai'".
    - This script PRE-FLIGHT CHECKS those packages and refuses to start (printing the
      exact pip command) rather than starting under a broken interpreter.
    - It starts uvicorn WITHOUT --reload on purpose: the reloader spawns a worker whose
      command line is opaque, which made a stale wrong-interpreter worker hard to kill
      (incident 2026-07-15). Edit -> re-run this script to pick up changes.
    - After start it VERIFIES the interpreter actually listening on the review port can
      import google.genai, catching the failure at startup instead of at first click.

  Usage:
    powershell -ExecutionPolicy Bypass -File graphrag\serve-all.ps1 -Open
    powershell -ExecutionPolicy Bypass -File graphrag\serve-all.ps1 -ChatPort 8790 -ReviewPort 8789
  Stop:
    powershell -ExecutionPolicy Bypass -File graphrag\stop-all.ps1
#>
param(
  [int]$ChatPort   = 8790,
  [int]$ReviewPort = 8789,
  [switch]$Open
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_ps_lib.ps1"

$graphrag = $PSScriptRoot                    # ...\llm_wiki\graphrag
$root     = Split-Path $graphrag -Parent     # ...\llm_wiki
$server   = Join-Path $graphrag 'agent\server.py'
$chatUrl  = "http://127.0.0.1:$ChatPort/"
$reviewUrl= "http://127.0.0.1:$ReviewPort/"

# ── 1+2) Resolve `py` launcher and pre-flight (py -3.14 must have fastapi/uvicorn/google-genai) ──
try {
  $py = Resolve-Py314 -RequireModules @('fastapi', 'uvicorn', 'google.genai')
} catch {
  Write-Host ""
  Write-Host "  [ERROR] $($_.Exception.Message)" -ForegroundColor Red
  Write-Host ""
  exit 1
}

# ── 3) Warn (not fail) if the Gemini key is empty ──
$envFile = Join-Path $graphrag '.env'
$hasKey = $false
if (Test-Path $envFile) { $hasKey = (Get-Content $envFile | Where-Object { $_ -match '^\s*GEMINI_API_KEY=\S' }).Count -gt 0 }
if (-not $hasKey) {
  Write-Host "  [WARN] GEMINI_API_KEY is empty in graphrag/.env." -ForegroundColor Yellow
  Write-Host "         Chat 'Gemini mode' and CQ/ontology generation will fail until you set it." -ForegroundColor Yellow
}

# ── 4) Stop any existing instances (robust: by port + process tree) ──
Stop-GraphRagServers -Ports @($ReviewPort, $ChatPort) | Out-Null

# ── 5) Start Review UI (FastAPI) on $ReviewPort, under py -3.14, NO --reload ──
Start-Process -FilePath $py `
  -ArgumentList '-3.14','-m','uvicorn','review.main:app','--host','127.0.0.1','--port',"$ReviewPort" `
  -WorkingDirectory $graphrag -WindowStyle Hidden | Out-Null

# ── 6) Start Chat / Gemini backend on $ChatPort, under py -3.14 ──
Start-Process -FilePath $py `
  -ArgumentList '-3.14',$server,"$ChatPort",$root `
  -WorkingDirectory $graphrag -WindowStyle Hidden | Out-Null

# ── 7) Readiness checks ──
$reviewOk = Wait-HttpOk $reviewUrl
$chatOk   = Wait-HttpOk $chatUrl

# ── 8) Verify the interpreter actually serving the review port can import google.genai ──
$reviewInterpOk = $false
if ($reviewOk) { $reviewInterpOk = Assert-ListenerInterpreter -Port $ReviewPort -RequireModules @('fastapi', 'uvicorn', 'google.genai') }

$reviewMsg = if ($reviewOk) { if ($reviewInterpOk) { 'OK' } else { 'UP but WRONG INTERPRETER' } } else { 'NOT READY' }
$reviewCol = if ($reviewOk -and $reviewInterpOk) { 'Green' } else { 'Yellow' }
$chatMsg   = if ($chatOk)   { 'OK' } else { 'NOT READY' }
$chatCol   = if ($chatOk)   { 'Green' } else { 'Yellow' }
Write-Host ""
Write-Host "  Review UI (FastAPI)  : $reviewUrl   $reviewMsg" -ForegroundColor $reviewCol
Write-Host "  Chat / Gemini backend: $chatUrl   $chatMsg" -ForegroundColor $chatCol
Write-Host ""
Write-Host "  Open the chat:            $chatUrl"                       -ForegroundColor Cyan
Write-Host "  Review UI (single-origin): ${chatUrl}review/  or direct $reviewUrl"
Write-Host "  Stop both: powershell -ExecutionPolicy Bypass -File graphrag\stop-all.ps1"
if ($Open -and $chatOk) { Start-Process $chatUrl }
