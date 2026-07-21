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

echo [2] Checking dependencies...
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
REM Try to install deps. If offline, skip but allow server to start anyway
REM (server uses lazy import for network-only libs, so missing packages
REM  only affect online features, not local dashboard)
"%VENV_PY%" -m pip install -r "%REQ_FILE%" --upgrade >nul 2>&1
if errorlevel 1 (
    echo    [WARN] pip install failed (offline or mirror down).
    echo    Trying fast core-only install without network...
    "%VENV_PY%" -m pip install fastapi uvicorn jinja2 pydantic python-multipart >nul 2>&1
    if errorlevel 1 (
        echo    [WARN] Offline install also failed. Will try to start anyway.
        echo    Online features may not work; local dashboard / DB still works.
    ) else (
        echo    Core deps installed (offline). Online features may be limited.
    )
) else (
    echo    Dependencies installed.
)

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