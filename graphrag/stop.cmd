@echo off
REM ダブルクリックでチャットサーバーを停止
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
echo.
pause
