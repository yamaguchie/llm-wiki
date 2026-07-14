@echo off
REM ダブルクリックでチャットサーバーを起動し、ブラウザを開く
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0serve.ps1" -Open
echo.
pause
