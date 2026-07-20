
#!/bin/bash

echo "========================================"
echo "   A股复盘工具"
echo "========================================"
echo ""

# 检查Python是否安装
if ! command -v python3 &> /dev/null; then
    echo "[错误] Python3未安装"
    echo "请先安装Python 3.8+"
    exit 1
fi

echo "[1] 检查虚拟环境..."
if [ ! -d "venv" ]; then
    echo "   创建虚拟环境..."
    python3 -m venv venv
    echo "   虚拟环境创建完成"
fi

echo "[2] 激活虚拟环境..."
source venv/bin/activate

echo "[3] 安装依赖..."
pip install -q -r requirements.txt --upgrade
echo "   依赖安装完成"

echo ""
echo "[4] 启动服务..."
echo "========================================"
echo "服务已启动！"
echo "访问地址: http://localhost:8000"
echo "按 Ctrl+C 停止服务"
echo "========================================"
echo ""

python start.py
