
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import uvicorn
except ImportError:
    print("ERROR: uvicorn 未安装。请先运行: pip install uvicorn")
    print("(如果当前无网, 可临时手动安装 wheel 或拷贝 venv 后重试)")
    sys.exit(1)

if __name__ == "__main__":
    try:
        uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
    except ImportError as e:
        print(f"ERROR: 启动失败, 缺少依赖: {e}")
        print("提示: 即使在线抓取依赖缺失, 本地数据库/页面仍可正常使用。")
        print("      请检查 src/main.py 的导入是否能正常加载。")
        sys.exit(1)
