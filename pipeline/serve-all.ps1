<#
  Start the UNIFIED GraphRAG server (single port).

  As of the 2026-07-15 unification there is ONE server:
    * review.main:app (FastAPI) on port 8790 — serves the chat (/), every review
      screen (/raw /cq /kg /validation /ontology-def /ontology-graph /llmwiki and the
      /review/* catch-all) AND every API (/api/ask /api/rag /api/kg/* /api/validation/*
      /api/raw/* /api/ontology/*). Same-origin, so the front-end uses API=''.
    Port 8789 and agent/server.py are RETIRED (review.main imports the agent directly).

  Must run under `py -3.14` (the interpreter that has fastapi + uvicorn + google-genai).
  Started WITHOUT --reload on purpose (reload workers were hard to kill; incident 2026-07-15).
  Edit -> re-run this script to pick up changes.

  Usage:
    powershell -ExecutionPolicy Bypass -File graphrag\serve-all.ps1 -Open
    powershell -ExecutionPolicy Bypass -File graphrag\serve-all.ps1 -Port 8790
  Stop:
    powershell -ExecutionPolicy Bypass -File graphrag\stop-all.ps1
#>
param(
  [int]$Port = 8790,
  [switch]$Open
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_ps_lib.ps1"

$graphrag = $PSScriptRoot                    # ...\llm_wiki\graphrag
$url      = "http://127.0.0.1:$Port/"

# ── 1) Resolve `py` launcher and pre-flight (py -3.14 must have fastapi/uvicorn/google-genai) ──
try {
  $py = Resolve-Py314 -RequireModules @('fastapi', 'uvicorn', 'google.genai')
} catch {
  Write-Host ""
  Write-Host "  [ERROR] $($_.Exception.Message)" -ForegroundColor Red
  Write-Host ""
  exit 1
}

# ── 2) Warn (not fail) if the Gemini key is empty ──
$envFile = Join-Path $graphrag '.env'
$hasKey = $false
if (Test-Path $envFile) { $hasKey = (Get-Content $envFile | Where-Object { $_ -match '^\s*GEMINI_API_KEY=\S' }).Count -gt 0 }
if (-not $hasKey) {
  Write-Host "  [WARN] GEMINI_API_KEY is empty in graphrag/.env." -ForegroundColor Yellow
  Write-Host "         Chat / CQ / ontology generation will fail until you set it." -ForegroundColor Yellow
}

# ── 3) Stop any existing instance (this port + the retired 8789), robust: by port + process tree ──
Stop-GraphRagServers -Ports @($Port, 8789) | Out-Null

# ── 4) Start the unified server (review.main:app) on $Port, under py -3.14, NO --reload ──
Start-Process -FilePath $py `
  -ArgumentList '-3.14','-m','uvicorn','review.main:app','--host','127.0.0.1','--port',"$Port" `
  -WorkingDirectory $graphrag -WindowStyle Hidden | Out-Null

# ── 5) Readiness (review.main imports the agent, which loads embeddings — allow time) ──
$ok = Wait-HttpOk $url 40
$interpOk = $false
if ($ok) { $interpOk = Assert-ListenerInterpreter -Port $Port -RequireModules @('fastapi', 'uvicorn', 'google.genai') }

$msg = if ($ok) { if ($interpOk) { 'OK' } else { 'UP but WRONG INTERPRETER' } } else { 'NOT READY' }
$col = if ($ok -and $interpOk) { 'Green' } else { 'Yellow' }
Write-Host ""
Write-Host "  統合サーバ review.main:app : $url   $msg" -ForegroundColor $col
Write-Host ""
Write-Host "  チャット:          $url"                    -ForegroundColor Cyan
Write-Host "  レビュー画面(例):  ${url}validation  /  ${url}raw  /  ${url}kg  /  ${url}ontology-def"
Write-Host "  停止: powershell -ExecutionPolicy Bypass -File graphrag\stop-all.ps1"
if ($Open -and $ok) { Start-Process $url }
