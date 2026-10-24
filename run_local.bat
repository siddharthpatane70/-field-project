@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if defined PYTHON_CMD goto python_found
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if defined PYTHON_CMD goto python_found

echo Python 3.10 or newer was not found. Install Python and enable the Python launcher or Add Python to PATH.
pause
exit /b 1

:python_found
%PYTHON_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto version_failed

if not exist ".venv\Scripts\python.exe" (
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto setup_failed
)

".venv\Scripts\python.exe" -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo Installing the app's Python dependency...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto setup_failed
)

".venv\Scripts\python.exe" app.py
exit /b %errorlevel%

:setup_failed
echo The app could not prepare its local Python environment. Check your internet connection and Python installation, then run this file again.
pause
exit /b 1

:version_failed
echo Python 3.10 or newer is required. Install a supported version, then run this file again.
pause
exit /b 1
