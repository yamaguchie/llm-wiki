<#
  Gemini-mode backend (agent/server.py): serves the chat + /api/ask (LLM planner/critic/answer + neural embeddings).
  Requires graphrag/.env with a valid GEMINI_API_KEY and `pip install google-genai`.
  Usage:
    powershell -ExecutionPolicy Bypass -File graphrag\serve-gemini.ps1 -Open
    powershell -ExecutionPolicy Bypass -File graphrag\serve-gemini.ps1 -Port 8790
#>
param([int]$Port = 8790, [switch]$Open)
$ErrorActionPreference = 'Stop'
$root   = Split-Path $PSScriptRoot -Parent
$server = Join-Path $PSScriptRoot 'agent\server.py'
$url    = "http://127.0.0.1:$Port/graphrag/chat/index.html"

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*server.py*' -and $_.CommandLine -like "*$Port*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 400

$launcher = $null; $pre = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3.14 --version *> $null 2>&1
  if ($LASTEXITCODE -eq 0) { $launcher = 'py'; $pre = @('-3.14') } else { $launcher = 'py' }
} elseif (Get-Command python -ErrorAction SilentlyContinue) { $launcher = 'python' } else { Write-Error 'Python not found'; exit 1 }
$allArgs = $pre + @($server, "$Port", $root)

$proc = Start-Process -FilePath $launcher -ArgumentList $allArgs -WorkingDirectory $root -WindowStyle Hidden -PassThru
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
  try { if ((Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { $ok = $true; break } }
  catch { Start-Sleep -Milliseconds 400 }
}
if ($ok) {
  Write-Host ""
  Write-Host "  Gemini backend started (PID $($proc.Id), port $Port)" -ForegroundColor Green
  Write-Host "  Open the chat and turn ON the 'Geminiモード' checkbox:" -ForegroundColor Cyan
  Write-Host "    $url"
  Write-Host "  Stop: powershell -ExecutionPolicy Bypass -File graphrag\stop.ps1 -All"
  if ($Open) { Start-Process $url }
} else {
  Write-Host "  Readiness check failed (port $Port in use?)." -ForegroundColor Yellow
}
