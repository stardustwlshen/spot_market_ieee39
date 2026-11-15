@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\launch.py" %*
) else (
  python "scripts\launch.py" %*
)
if errorlevel 1 pause
