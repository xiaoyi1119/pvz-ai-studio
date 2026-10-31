@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean pvz_ai_studio.spec
if errorlevel 1 exit /b %errorlevel%
echo.
echo Build complete: dist\PVZ AI Studio\PVZ AI Studio.exe

