@echo off
REM ===== Fupan Startup Script (ASCII only to avoid GBK encoding issues) =====
REM Use absolute paths to bypass PATH pollution (e.g. D:\AI\deepseek_A\venv\Scripts)
chcp 65001 > nul

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%SCRIPT_DIR%requirements.txt"
set "START_PY=%SCRIPT_DIR%start.py"

echo ========================================
echo    A-Share Review Tool
echo ========================================
echo.

echo [1] Checking virtual environment...
if not exist "%VENV_PY%" (
    echo    Venv missing, creating...
    py -3.10 -m venv "%VENV_DIR%" 2>nul
    if not exist "%VENV_PY%" (
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" -m venv "%VENV_DIR%" 2>nul
    )
    if not exist "%VENV_PY%" (
        echo [ERROR] Failed to create venv. Please install Python 3.10.
        pause
        exit /b 1
    )
    echo    Venv created.
)
echo    Python: %VENV_PY%

echo [2] Installing dependencies...
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r "%REQ_FILE%" --upgrade
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)
echo    Dependencies installed.

echo.
echo [3] Starting server...
echo ========================================
echo Server started!
echo URL: http://localhost:8000
echo Press Ctrl+C to stop.
echo ========================================
echo.

"%VENV_PY%" "%START_PY%"

pause