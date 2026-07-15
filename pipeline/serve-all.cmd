@echo off
REM Double-click to start BOTH servers (chat 8790 + review UI 8789) and open the chat.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0serve-all.ps1" -Open
echo.
pause
