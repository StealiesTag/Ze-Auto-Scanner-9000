@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 11))"
        if errorlevel 1 goto python_version
        py -3 -m venv .venv
    ) else (
        where python >nul 2>nul
        if errorlevel 1 goto python_missing
        python -c "import sys; raise SystemExit(sys.version_info < (3, 11))"
        if errorlevel 1 goto python_version
        python -m venv .venv
    )
    if errorlevel 1 goto setup_failed
)

if not exist ".venv\.paparazzi_packages_ready" (
    echo Installing Paparazzi for first use. This may take a few minutes...
    ".venv\Scripts\python.exe" -m pip install -r requirements-local.txt
    if errorlevel 1 goto setup_failed
    echo ready>".venv\.paparazzi_packages_ready"
)

".venv\Scripts\python.exe" -c "import zeroconf" >nul 2>nul
if errorlevel 1 (
    echo Installing updated Paparazzi packages...
    ".venv\Scripts\python.exe" -m pip install -r requirements-local.txt
    if errorlevel 1 goto setup_failed
)

set PAPARAZZI_OPEN_BROWSER=1
echo Starting Paparazzi. Keep this window open while you use the app.
".venv\Scripts\python.exe" app.py
echo Paparazzi has stopped.
pause
exit /b 0

:python_missing
echo Python 3.11 or newer is required. Install Python from https://www.python.org/downloads/windows/ and run this file again.
pause
exit /b 1

:python_version
echo Python 3.11 or newer is required. Install a current Python version and run this file again.
pause
exit /b 1

:setup_failed
echo Setup did not finish. Check your internet connection and the error above, then run this file again.
pause
exit /b 1
