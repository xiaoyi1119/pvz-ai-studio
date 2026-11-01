@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run.py
) else if exist "..\LLM_PvZ_Player\.venv\Scripts\python.exe" (
  "..\LLM_PvZ_Player\.venv\Scripts\python.exe" run.py
) else (
  python run.py
)

