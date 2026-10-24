@echo off
rem Double-click to start TRUSTA on Windows. The first run opens the setup wizard.
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if exist "runtime\python\python.exe" set "PY=%~dp0runtime\python\python.exe"
set "TRUSTA_PYTHON=%PY%"
if not exist ".env" (
  echo 首次运行：配置向导
  "%PY%" -m scripts.setup
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
