@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python not found. Install Python 3.11+ from python.org first.
    pause
    exit /b 1
)

if not exist .venv (
    echo [1/3] Creating virtual environment...
    python -m venv .venv
)

echo [2/3] Installing dependencies...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo [!] Dependency install failed. Check your internet connection.
    pause
    exit /b 1
)

echo [3/3] Preparing database...
.venv\Scripts\python.exe scripts\seed_minimal.py

echo.
echo ==================================================
echo   FrontDesk AI  -  http://127.0.0.1:8000
echo   Keep this window open. Ctrl+C or close = stop.
echo ==================================================
echo.

start "" cmd /c "timeout /t 4 >nul & start "" http://127.0.0.1:8000"
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
pause
