@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 (
  echo Cannot open this project folder. Copy it to a local drive and retry.
  pause
  exit /b 1
)
rem ASCII wrapper avoids cmd.exe code-page problems on first launch.
where py >nul 2>&1
if errorlevel 1 goto try_python
py -3.13 -c "import sys, venv, ensurepip; raise SystemExit(sys.implementation.name != 'cpython')" >nul 2>&1
if not errorlevel 1 (
  py -3.13 scripts\launch_app.py %*
  goto finish
)
py -3.12 -c "import sys, venv, ensurepip; raise SystemExit(sys.implementation.name != 'cpython')" >nul 2>&1
if not errorlevel 1 (
  py -3.12 scripts\launch_app.py %*
  goto finish
)
:try_python
python -c "import sys, venv, ensurepip; raise SystemExit(sys.implementation.name != 'cpython' or sys.version_info[:2] not in ((3,12),(3,13)))" >nul 2>&1
if errorlevel 1 goto no_python
python scripts\launch_app.py %*
goto finish
:no_python
echo CPython 3.12 or 3.13 with venv and ensurepip was not found.
echo Install from https://www.python.org/downloads/windows/ and enable Add Python to PATH.
echo Python 3.11 is incompatible with the pinned scipy/networkx versions.
echo Your scenarios, quotations and results have not been changed.
pause
exit /b 1
:finish
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" pause
exit /b %APP_EXIT%
