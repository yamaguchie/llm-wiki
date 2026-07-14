@echo off
REM ダブルクリックで Gemini バックエンドを起動し、ブラウザを開く（Geminiモードのチェックをオンに）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0serve-gemini.ps1" -Open
echo.
pause
