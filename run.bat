@echo off
REM ===== Fupan Startup Script =====
REM Skips pip install when deps are already satisfied (most common path).
REM Use absolute paths to bypass PATH pollution.

chcp 65001 > nul

setlocal

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%SCRIPT_DIR%requirements.txt"
set "START_PY=%SCRIPT_DIR%start.py"

echo ========================================
echo    A-Share Review Tool
echo ========================================
echo.

REM ---- Sanity check: script files exist ----
if not exist "%START_PY%" (
    echo [ERROR] start.py not found: %START_PY%
    echo Press any key to exit...
    pause > nul
    exit /b 1
)

echo [1] Checking virtual environment...
if not exist "%VENV_PY%" (
    echo    Venv missing, creating...
    py -3.10 -m venv "%VENV_DIR%"
    if not exist "%VENV_PY%" (
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" -m venv "%VENV_DIR%"
    )
    if not exist "%VENV_PY%" (
        echo [ERROR] Failed to create venv. Please install Python 3.10.
        echo Press any key to exit...
        pause > nul
        exit /b 1
    )
    echo    Venv created.
)
echo    Python: %VENV_PY%
"%VENV_PY%" --version

echo.
echo [2] Checking dependencies...
echo    Probing core imports: fastapi, uvicorn, jinja2, pydantic ...
"%VENV_PY%" -c "import fastapi, uvicorn, jinja2, pydantic" 1>nul 2>nul
if errorlevel 1 (
    echo    [WARN] Core deps missing. Installing - timeout 60s ...
    "%VENV_PY%" -m pip install fastapi uvicorn jinja2 pydantic python-multipart --disable-pip-version-check --timeout 60
    if errorlevel 1 (
        echo    [WARN] Core install failed. Will try to start anyway.
        echo    Online / parsing features may be limited.
    ) else (
        echo    Core deps installed.
    )
) else (
    echo    [OK] Core deps satisfied. Skipping pip install.
)
echo    Probing optional imports: akshare, pandas, dotenv, requests, numpy ...
"%VENV_PY%" -c "import akshare, pandas, dotenv, requests, numpy" 1>nul 2>nul
if errorlevel 1 (
    echo    [WARN] Some optional deps missing. Installing full requirements.txt - timeout 60s ...
    if exist "%REQ_FILE%" (
        "%VENV_PY%" -m pip install -r "%REQ_FILE%" --upgrade --disable-pip-version-check --timeout 60
        if errorlevel 1 (
            echo    [WARN] Full install failed. Continuing with available deps.
        ) else (
            echo    Optional deps installed.
        )
    ) else (
        echo    [WARN] requirements.txt missing. Skipping optional install.
    )
) else (
    echo    [OK] Optional deps satisfied.
)

echo.
echo [3] Starting server at http://localhost:8000 ...
echo ========================================
echo Server running. Press Ctrl+C in this window to stop.
echo ========================================
echo.

REM Run start.py. Output stays on screen for visibility.
"%VENV_PY%" "%START_PY%"
set "RC=%errorlevel%"

echo.
echo ========================================
echo Server stopped (exit code: %RC%).
echo Press any key to close this window...
pause > nul

endlocal
