@echo off
REM ===== Fupan Diagnostic Script =====
REM Run this BEFORE run.bat to see exactly what's wrong.

echo ========================================
echo    Fupan Diagnostic Check
echo ========================================
echo.

set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%venv\Scripts\python.exe"

echo [1] Checking Python venv...
if not exist "%VENV_PY%" (
    echo    [FAIL] Python venv not found: %VENV_PY%
    echo           Please install Python 3.10 first.
    goto :end
)
echo    [OK] Venv exists.

echo.
echo [2] Checking Python version...
"%VENV_PY%" --version
if errorlevel 1 (
    echo    [FAIL] Python interpreter cannot run.
    goto :end
)

echo.
echo [3] Checking core dependencies...
"%VENV_PY%" -c "import fastapi; print('    fastapi:', fastapi.__version__)" 2>nul
if errorlevel 1 (
    echo    [FAIL] fastapi not installed.
    echo           Run: "%VENV_PY%" -m pip install fastapi uvicorn jinja2 pydantic python-multipart
    goto :end
)
"%VENV_PY%" -c "import uvicorn; print('    uvicorn:', uvicorn.__version__)" 2>nul
if errorlevel 1 (
    echo    [FAIL] uvicorn not installed.
    goto :end
)
"%VENV_PY%" -c "import jinja2; print('    jinja2:', jinja2.__version__)" 2>nul
if errorlevel 1 (
    echo    [FAIL] jinja2 not installed.
    goto :end
)
"%VENV_PY%" -c "import pydantic; print('    pydantic:', pydantic.VERSION)" 2>nul
if errorlevel 1 (
    echo    [FAIL] pydantic not installed.
    goto :end
)
echo    [OK] All core deps installed.

echo.
echo [4] Checking optional dependencies...
"%VENV_PY%" -c "import akshare; print('    akshare: OK')" 2>nul
if errorlevel 1 echo    [INFO] akshare not installed (online fetch will not work)
"%VENV_PY%" -c "import pandas; print('    pandas: OK')" 2>nul
if errorlevel 1 echo    [INFO] pandas not installed (limited data processing)
"%VENV_PY%" -c "import dotenv; print('    python-dotenv: OK')" 2>nul
if errorlevel 1 echo    [INFO] python-dotenv not installed (.env.1 will be ignored)
"%VENV_PY%" -c "import requests; print('    requests: OK')" 2>nul
if errorlevel 1 echo    [INFO] requests not installed (HTTP fetch limited)

echo.
echo [5] Checking port 8000...
netstat -ano | findstr ":8000.*LISTENING" >nul
if errorlevel 1 (
    echo    [OK] Port 8000 is free.
) else (
    echo    [WARN] Port 8000 is occupied. Server may fail to start.
)

echo.
echo [6] Checking project files...
if exist "%SCRIPT_DIR%start.py" echo    [OK] start.py
if exist "%SCRIPT_DIR%requirements.txt" echo    [OK] requirements.txt
if exist "%SCRIPT_DIR%src\main.py" echo    [OK] src/main.py
if exist "%SCRIPT_DIR%data\fupan.db" (
    echo    [OK] data\fupan.db
) else (
    echo    [INFO] No database yet, will be created on first run.
)

echo.
echo ========================================
echo    Diagnostic complete.
echo ========================================
echo If all checks above are OK, you can run run.bat.
echo.

:end
echo Press any key to close...
pause > nul
