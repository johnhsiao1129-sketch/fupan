@echo off
chcp 65001 > nul
echo ========================================
echo    A股复盘工具
echo ========================================
echo.

REM 用绝对路径定位 venv, 避免 PATH 污染 (如 D:\AI\deepseek_A\venv\Scripts 抢占)
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%SCRIPT_DIR%requirements.txt"
set "START_PY=%SCRIPT_DIR%start.py"

echo [1] 检查虚拟环境...
if not exist "%VENV_PY%" (
    echo    虚拟环境不存在, 创建中...
    REM 优先用 py launcher 显式指定 Python 3.10 (绕开 PATH 污染)
    py -3.10 -m venv "%VENV_DIR%" 2>nul
    if not exist "%VENV_PY%" (
        REM fallback: 用系统 python
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" -m venv "%VENV_DIR%" 2>nul
    )
    if not exist "%VENV_PY%" (
        echo [错误] 虚拟环境创建失败, 请确认 Python 3.10 已安装
        pause
        exit /b 1
    )
    echo    虚拟环境创建完成
)
echo    Python: %VENV_PY%

echo [2] 安装依赖...
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r "%REQ_FILE" --upgrade
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)
echo    依赖安装完成

echo.
echo [3] 启动服务...
echo ========================================
echo 服务已启动！
echo 访问地址: http://localhost:8000
echo 按 Ctrl+C 停止服务
echo ========================================
echo.

"%VENV_PY%" "%START_PY%"

pause