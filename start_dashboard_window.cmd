@echo off
setlocal
cd /d "%~dp0"

set "DASHBOARD_URL=http://127.0.0.1:8090/?ui=10"
set "DASHBOARD_PROFILE=%~dp0.edge-dashboard-profile"
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" goto edge_x86
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" goto edge_x64
goto browser_fallback

:edge_x86
start "PVZ AI Studio" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" --app="%DASHBOARD_URL%" --user-data-dir="%DASHBOARD_PROFILE%" --window-size=1180,720 --window-position=700,70
exit /b 0

:edge_x64
start "PVZ AI Studio" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" --app="%DASHBOARD_URL%" --user-data-dir="%DASHBOARD_PROFILE%" --window-size=1180,720 --window-position=700,70
exit /b 0

:browser_fallback
start "PVZ AI Studio" "%DASHBOARD_URL%"

