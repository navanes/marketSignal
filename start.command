#!/bin/zsh
set -e

cd "$(dirname "$0")"

PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "Python was not found. Please install Python 3, then run this file again."
    echo
    read "reply?Press Return to close this window..."
    exit 1
fi

if [ ! -f ".venv/bin/activate" ]; then
    echo "Creating virtual environment..."
    "$PYTHON_CMD" -m venv .venv
fi

source ".venv/bin/activate"

echo "Installing/updating dependencies..."
python -m pip install --upgrade pip
python -m pip install -r webapp/requirements.txt

echo "Installing Playwright browser files..."
python -m playwright install chromium

echo "Starting Freight Quote Agent..."
open "http://127.0.0.1:8001"
python run_webapp.py

echo
read "reply?Press Return to close this window..."
