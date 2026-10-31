@echo off
setlocal
cd /d "%~dp0"

rem Start the backend only when port 8090 is not ready.
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8090/api/status' -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  start "PVZ Agent Plugin" /min "%~dp0start_plugin.cmd"
  timeout /t 2 /nobreak >nul
)

call "%~dp0start_dashboard_window.cmd"

