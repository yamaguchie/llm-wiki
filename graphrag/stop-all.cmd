@echo off
REM Double-click to stop BOTH servers.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-all.ps1"
echo.
pause
