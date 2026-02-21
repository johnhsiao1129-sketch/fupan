# GitHub 文件上传记录

> 本文件记录了哪些文件会被提交到GitHub，哪些会被忽略
> 更新时间：2026-02-22

---

## ✅ 会上传到GitHub的文件

### 核心代码
- ✅ `src/*.py` - 所有Python源代码
- ✅ `data/database.py` - 数据库初始化脚本
- ✅ `src/main.py` - FastAPI主程序
- ✅ `src/db_operations.py` - 数据库操作
- ✅ `src/data_acquisition.py` - 数据获取服务
- ✅ `src/market_mood_calculator.py` - 市场情绪计算

### 前端文件
- ✅ `templates/dashboard.html` - 主页面
- ✅ `static/*` - 静态资源（JS/CSS/HTML测试文件除外）
- ✅ `static/chart.umd.min.js` - Chart.js图表库
- ✅ `static/sliding-window.js` - 可拖拽排序脚本

### 配置文件
- ✅ `.env.example` - 环境变量示例（不含真实密钥）
- ✅ `requirements.txt` - Python依赖列表
- ✅ `.gitignore` - Git忽略规则
- ✅ `AGENTS.md` - 项目规范文档
- ✅ `README.md` - 项目说明文档

### 数据文件
- ✅ `data/fupan.db` - 主数据库（公开的大A数据）
- ✅ `data/.gitkeep` - 保留data目录

### 启动脚本
- ✅ `start.py` - 项目启动脚本
- ✅ `run.bat` - Windows启动脚本
- ✅ `run.sh` - Linux/Mac启动脚本
- ✅ `verify_db.py` - 数据库验证脚本

---

## ❌ 不会上传到GitHub的文件

### 敏感配置
- ❌ `.env` - 包含真实的API密钥
- ❌ `*.key` - 密钥文件
- ❌ `credentials.json` - 凭证文件

### 备份文件
- ❌ `backup_*` - 所有备份文件夹及其内容
- ❌ `backup_*.html` - 备份的HTML文件

### 临时数据
- ❌ `data/*.json` - JSON格式的数据文件
- ❌ `*.log` - 日志文件
- ❌ `*.tmp` - 临时文件
- ❌ `*.temp` - 临时文件

### Python缓存
- ❌ `__pycache__/` - Python字节码缓存
- ❌ `*.pyc` - 编译的Python文件
- ❌ `*.pyo` - 优化的Python文件

### IDE配置
- ❌ `.vscode/` - VSCode配置
- ❌ `.idea/` - PyCharm配置
- ❌ `*.swp` - Vim临时文件
- ❌ `*~` - 备份文件

### 虚拟环境
- ❌ `venv/` - Python虚拟环境
- ❌ `env/` - Python虚拟环境
- ❌ `.venv` - Python虚拟环境

### 系统文件
- ❌ `.DS_Store` - Mac系统文件
- ❌ `Thumbs.db` - Windows缩略图缓存

---

## 📝 添加新文件时的规则

### 如果要上传：
1. 放在项目的合适位置（代码在src/，静态文件在static/）
2. 不要包含敏感信息（密码、密钥、个人数据）
3. 如果是配置文件，创建 `.example` 版本

### 如果不想上传：
1. 将其路径添加到 `.gitignore` 文件
2. 格式：`要忽略的文件/目录`
3. 支持 `*.扩展名` 和 `目录/` 两种格式

---

## 🔍 查看当前Git状态

```bash
# 查看将被上传的文件（绿色表示新增，红色表示被忽略）
git status

# 查看详细的忽略规则
git check-ignore -v *

# 测试某个文件是否会被忽略
git check-ignore 文件名
```

---

## 📊 当前文件统计

```bash
# 查看项目文件总数
find . -type f ! -path "./.git/*" ! -path "./backup_*/*" | wc -l

# 查看被忽略的文件数
git check-ignore -v * | wc -l
```

---

**重要提示**：
- 本文件需要随着项目发展持续更新
- 每次添加新功能时，检查是否需要更新此文件
- 如有疑问，查看 `.gitignore` 文件确认忽略规则
