@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\launch_sichuan.py" %*
) else (
  python "scripts\launch_sichuan.py" %*
)
if errorlevel 1 pause
