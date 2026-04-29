@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_CMD=python"
where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo Python was not found. Please install Python 3, then run this file again.
        pause
        exit /b 1
    )
    set "PYTHON_CMD=py -3"
)

if not exist ".venv\Scripts\activate.bat" (
    echo Creating virtual environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Failed to activate the virtual environment.
    pause
    exit /b 1
)

echo Installing/updating dependencies...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip.
    pause
    exit /b 1
)

python -m pip install -r webapp\requirements.txt
if errorlevel 1 (
    echo Failed to install Python dependencies.
    pause
    exit /b 1
)

echo Installing Playwright browser files...
python -m playwright install chromium
if errorlevel 1 (
    echo Failed to install Playwright browsers.
    pause
    exit /b 1
)

echo Starting Freight Quote Agent...
start "" "http://127.0.0.1:8001"
python run_webapp.py

pause
