# 项目结构说明

```
fupan/                          # 项目根目录
│
├── src/                         # 源代码目录
│   ├── __init__.py              # 包初始化文件
│   ├── main.py                  # FastAPI主程序（后端核心）
│   └── data_client.py           # 数据获取客户端
│
├── templates/                   # HTML模板目录
│   └── dashboard.html           # 前端主页面
│
├── static/                      # 静态文件目录
│   ├── css/                     # CSS样式文件
│   ├── js/                      # JavaScript文件
│   └── images/                  # 图片文件
│
├── data/                        # 数据存储目录
│   ├── .gitkeep                 # 保持目录结构
│   └── analysis.json            # 分析数据缓存
│
├── venv/                        # 虚拟环境目录（自动生成）
│   ├── Scripts/                 # Windows脚本
│   │   ├── activate.bat         # 激活脚本
│   │   └── python.exe           # Python解释器
│   └── lib/                     # 库文件
│
├── logs/                        # 日志目录（自动生成）
│   ├── app.log                  # 应用日志
│   └── error.log                # 错误日志
│
├── start.py                     # 程序启动脚本
├── run.bat                      # Windows启动脚本
├── run.sh                       # Linux/Mac启动脚本
├── test.py                      # 测试脚本
├── requirements.txt             # Python依赖列表
├── pyproject.toml               # 项目配置（Poetry）
├── .env.example                 # 环境变量示例
├── .gitignore                   # Git忽略文件
│
├── README.md                    # 项目说明文档
├── USAGE.md                     # 使用指南
├── CHECKLIST.md                 # 启动检查清单
└── PROJECT_STRUCTURE.md         # 项目结构说明（本文件）
```

## 文件说明

### 核心文件

#### `src/main.py`
- **作用**: FastAPI应用主程序
- **功能**:
  - 定义API路由
  - 处理HTTP请求
  - 数据整合与返回
  - 前端服务

#### `src/data_client.py`
- **作用**: 数据获取客户端
- **功能**:
  - 连接数据源API
  - 获取实时股价数据
  - 数据清洗与转换
  - 异步请求处理

#### `templates/dashboard.html`
- **作用**: 前端主页面
- **功能**:
  - 用户界面展示
  - 数据可视化
  - 用户交互
  - 图表渲染

### 启动文件

#### `start.py`
- **作用**: Python启动脚本
- **使用**: `python start.py`
- **功能**: 启动FastAPI开发服务器

#### `run.bat` / `run.sh`
- **作用**: 一键启动脚本
- **使用**: 双击或执行
- **功能**:
  - 创建虚拟环境
  - 安装依赖
  - 启动服务

### 配置文件

#### `requirements.txt`
- **作用**: Python依赖列表
- **内容**: 所有需要的包及版本

#### `.env.example`
- **作用**: 环境变量示例
- **用途**: 配置系统参数

#### `.gitignore`
- **作用**: Git忽略规则
- **内容**: 不需要版本控制的文件

### 文档文件

#### `README.md`
- **作用**: 项目说明
- **内容**: 功能介绍、快速开始等

#### `USAGE.md`
- **作用**: 详细使用指南
- **内容**: 操作说明、技巧等

#### `CHECKLIST.md`
- **作用**: 启动检查清单
- **内容**: 安装步骤、故障排除

### 测试文件

#### `test.py`
- **作用**: 系统测试脚本
- **功能**: 验证各模块功能

## 目录用途

### `src/`
存放所有源代码，保持项目结构清晰。

### `templates/`
存放HTML模板，前端页面文件。

### `static/`
静态资源：
- CSS样式表
- JavaScript脚本
- 图片资源

### `data/`
数据存储：
- 历史数据缓存
- 用户分析记录
- 配置文件

### `venv/`
虚拟环境：
- Python解释器
- 依赖包
- 运行环境

### `logs/`
日志文件：
- 应用日志
- 错误日志
- 调试信息

## 数据流程

```
用户访问
  ↓
FastAPI接收请求
  ↓
StockDataService获取数据
  ↓
调用数据源API（新浪/东方财富/腾讯）
  ↓
数据处理与分析
  ↓
返回JSON数据
  ↓
前端渲染（dashboard.html）
  ↓
用户查看交互
```

## 工作流程

### 启动流程
1. 运行 `run.bat` 或 `run.sh`
2. 检查虚拟环境
3. 安装依赖
4. 启动 `start.py`
5. FastAPI服务启动
6. 监听8000端口

### 数据更新流程
1. 用户点击刷新
2. 前端发送API请求
3. 后端调用数据源
4. 处理返回数据
5. 更新缓存
6. 返回新数据
7. 前端重新渲染

### 用户交互流程
1. 用户查看数据
2. 编辑分析内容
3. 触发保存事件
4. 数据保存到本地
5. 下次加载时读取

## 扩展指南

### 添加新功能
1. 在 `src/main.py` 添加API路由
2. 在 `src/data_client.py` 添加数据获取方法
3. 在 `templates/dashboard.html` 添加前端展示
4. 更新文档

### 添加新数据源
1. 在 `src/data_client.py` 添加新方法
2. 实现API调用逻辑
3. 数据清洗转换
4. 添加到主程序API

### 自定义样式
1. 修改 `templates/dashboard.html` 的CSS
2. 调整布局和颜色
3. 添加新的样式类

## 维护建议

### 代码规范
- 遵循PEP 8规范
- 添加必要的注释
- 保持函数简洁

### 性能优化
- 使用缓存减少API调用
- 优化数据库查询
- 压缩静态资源

### 安全建议
- 不要提交敏感信息
- 定期更新依赖包
- 使用环境变量管理配置

---

希望这个结构说明能帮助你更好地理解和使用项目！
