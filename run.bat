
@echo off
chcp 65001 > nul
echo ========================================
echo    A股复盘工具
echo ========================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] Python未安装或未添加到PATH环境变量
    echo 请先安装Python 3.8+
    pause
    exit /b 1
)

echo [1] 检查虚拟环境...
if not exist "venv" (
    echo    创建虚拟环境...
    python -m venv venv
    echo    虚拟环境创建完成
)

echo [2] 激活虚拟环境...
call venv\Scripts\activate.bat

echo [3] 安装依赖...
pip install -q -r requirements.txt --upgrade
echo    依赖安装完成

echo.
echo [4] 启动服务...
echo ========================================
echo 服务已启动！
echo 访问地址: http://localhost:8000
echo 按 Ctrl+C 停止服务
echo ========================================
echo.

python start.py

pause
