@echo off
setlocal
cd /d "%~dp0"
set "SC_PYTHON=python"
if exist ".venv\Scripts\python.exe" (
  set "SC_PYTHON=%~dp0.venv\Scripts\python.exe"
) else if exist "%USERPROFILE%\.conda\environments.txt" (
  for /f "usebackq delims=" %%E in ("%USERPROFILE%\.conda\environments.txt") do (
    if /i "%%~nxE"=="spot_market_ieee39" if exist "%%E\python.exe" set "SC_PYTHON=%%E\python.exe"
  )
)
"%SC_PYTHON%" "scripts\launch_sichuan.py" %*
set "SC_STATUS=%ERRORLEVEL%"
if not "%SC_STATUS%"=="0" if "%~1"=="" pause
exit /b %SC_STATUS%
