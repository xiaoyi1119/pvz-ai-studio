@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "PVZ AI Studio" ".venv\Scripts\pythonw.exe" desktop.py
) else (
  start "PVZ AI Studio" pythonw desktop.py
)

