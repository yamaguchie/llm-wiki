<#
  Shared helpers for the GraphRAG demo serve/stop scripts.
  Dot-source from a sibling script:   . "$PSScriptRoot\_ps_lib.ps1"

  Why this file exists (incident 2026-07-15):
    - There are TWO long-running servers:
        * Chat / Gemini backend : agent/server.py         on port 8790
        * Review UI (FastAPI)   : uvicorn review.main:app on port 8789
    - They MUST run under `py -3.14`, the only interpreter here with
      fastapi + uvicorn + google-genai. Bare `python` on this machine resolves to a
      uv cpython-3.11 that lacks google-genai -> "No module named 'google.genai'".
    - Stopping by command-line match alone is NOT enough: a uvicorn *reload worker*
      is spawned via multiprocessing and its command line contains neither
      'uvicorn' nor 'review.main:app'. If you only kill the parent, or match by name,
      a worker can keep holding the port. So we kill by PORT and by PROCESS TREE.
#>

# Canonical ports for the demo.
$Global:GR_CHAT_PORT   = 8790   # agent/server.py
$Global:GR_REVIEW_PORT = 8789   # uvicorn review.main:app

function Resolve-Py314 {
  <#
    Return the path to the `py` launcher, guaranteeing that `py -3.14` exists AND can
    import the required modules. Throws (with the exact pip command) otherwise.
    This is the ONLY sanctioned interpreter for the LLM-backed servers.
  #>
  param([string[]]$RequireModules = @('fastapi', 'uvicorn', 'google.genai'))
  $pyCmd = Get-Command py -ErrorAction SilentlyContinue
  if (-not $pyCmd) { throw "Python launcher 'py' not found on PATH. Install Python 3.14 from python.org." }
  & py -3.14 --version *> $null 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "py -3.14 is not available. Install Python 3.14, or edit these scripts to pin your interpreter."
  }
  if ($RequireModules -and $RequireModules.Count -gt 0) {
    $imp = ($RequireModules -join ', ')
    & py -3.14 -c "import $imp" 2>$null
    if ($LASTEXITCODE -ne 0) {
      $pipmods = ($RequireModules | ForEach-Object { if ($_ -eq 'google.genai') { 'google-genai' } else { $_ } }) -join ' '
      throw "py -3.14 is missing required packages ($imp).`r`n  Fix it with:  py -3.14 -m pip install $pipmods"
    }
  }
  return $pyCmd.Source
}

function Get-PortListenerPids {
  param([int]$Port)
  $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($c) { return @($c.OwningProcess | Select-Object -Unique) }
  return @()
}

function Stop-ProcessTree {
  <# Kill a PID and all of its descendants (children first). This is what reliably
     takes down a uvicorn reloader together with its spawned worker(s). #>
  param([int]$RootPid)
  if (-not $RootPid) { return }
  $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$RootPid" -ErrorAction SilentlyContinue
  foreach ($c in $children) { Stop-ProcessTree -RootPid ([int]$c.ProcessId) }
  Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

function Stop-GraphRagServers {
  <#
    Robustly stop the review UI + chat backend. Strategy (belt and suspenders):
      1) Kill the PROCESS TREE of whatever LISTENS on each port  -> frees the port even
         when the listener is a reload parent whose worker's command line is opaque.
      2) Kill the PROCESS TREE of anything whose command line matches our servers
         (catches a just-started parent that isn't listening yet).
      3) Re-check the ports and sweep any survivor.
    Returns the list of PIDs it targeted.
  #>
  param([int[]]$Ports = @($Global:GR_REVIEW_PORT, $Global:GR_CHAT_PORT))
  $targeted = @()

  # (1) by port
  foreach ($p in $Ports) {
    foreach ($id in (Get-PortListenerPids $p)) { $targeted += $id; Stop-ProcessTree -RootPid ([int]$id) }
  }

  # (2) by command line (parents that may not be listening yet)
  $byCmd = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -match 'agent[\\/]+server\.py' -or
      $_.CommandLine -match 'review\.main:app'
    }
  foreach ($proc in $byCmd) { $targeted += $proc.ProcessId; Stop-ProcessTree -RootPid ([int]$proc.ProcessId) }

  # (3) settle + sweep any survivor still holding a port
  Start-Sleep -Milliseconds 500
  foreach ($p in $Ports) {
    foreach ($id in (Get-PortListenerPids $p)) { $targeted += $id; Stop-ProcessTree -RootPid ([int]$id) }
  }
  Start-Sleep -Milliseconds 300

  return ($targeted | Where-Object { $_ } | Select-Object -Unique)
}

function Wait-HttpOk {
  param([string]$Url, [int]$Tries = 25, [int]$DelayMs = 400)
  for ($i = 0; $i -lt $Tries; $i++) {
    try { if ((Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { return $true } }
    catch { Start-Sleep -Milliseconds $DelayMs }
  }
  return $false
}

function Assert-ListenerInterpreter {
  <#
    After start, verify the process actually LISTENING on $Port can import the required
    modules. This catches the exact failure mode of the 2026-07-15 incident (a stale /
    wrong-interpreter worker holding the port) at startup instead of at first LLM click.
    Returns $true/$false; writes a red diagnostic on failure.
  #>
  param([int]$Port, [string[]]$RequireModules = @('fastapi', 'uvicorn', 'google.genai'))
  $listenerPid = (Get-PortListenerPids $Port | Select-Object -First 1)
  if (-not $listenerPid) { Write-Host "  [WARN] Nothing is listening on port $Port to verify." -ForegroundColor Yellow; return $false }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerPid" -ErrorAction SilentlyContinue
  $exe = if ($proc) { $proc.ExecutablePath } else { $null }
  if (-not $exe) { Write-Host "  [WARN] Could not resolve interpreter for PID $listenerPid on port $Port." -ForegroundColor Yellow; return $false }
  $imp = ($RequireModules -join ', ')
  & $exe -c "import $imp" 2>$null
  if ($LASTEXITCODE -eq 0) { return $true }
  Write-Host "  [ERROR] The server on port $Port is running under an interpreter that CANNOT import: $imp" -ForegroundColor Red
  Write-Host "          Interpreter: $exe" -ForegroundColor Red
  Write-Host "          This is the 'No module named google.genai' failure. Stop everything and re-run under py -3.14." -ForegroundColor Red
  return $false
}
