@echo off
REM 数据获取服务启动脚本 (Windows)

echo.
echo ========================================
echo 数据获取服务启动器
echo ========================================
echo.

echo 请选择启动模式:
echo 1. 启动定时任务服务 (日常使用)
echo 2. 测试模式 - 手动执行所有任务
echo 3. 运行完整测试
echo 4. 运行简化测试 (交互式)
echo.

set /p choice="请输入选项 (1-4): "

echo.

if "%choice%"=="1" (
    echo 启动定时任务服务...
    echo 按 Ctrl+C 停止服务
    echo.
    python src\scheduler.py
) else if "%choice%"=="2" (
    echo 执行测试模式...
    echo.
    python src\scheduler.py test
) else if "%choice%"=="3" (
    echo 运行完整测试...
    echo.
    python test_data_acquisition.py
) else if "%choice%"=="4" (
    echo 运行简化测试...
    echo.
    python test_simple_data.py
) else (
    echo 无效选项
    pause
    exit /b 1
)

pause
