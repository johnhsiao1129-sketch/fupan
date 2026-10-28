# AGENTS.md

This file contains conventions and commands for AI agents operating in this repository.

> **项目概述**: A股复盘数据系统（FastAPI + SQLite + akshare），涵盖首板/炸板/跌停/强势股数据抓取、板块溢价分析（T+1开盘 vs T收盘）与趋势评分。

## Build, Lint, and Test Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Start development server (auto-reload on file changes)
python start.py

# Run all tests
python -m pytest

# Run specific test files
pytest test_trend_api.py                       # trend stock API tests
pytest test_auto_create_topics.py              # auto-create topic tests
pytest test_save_limit_stats.py                # limit stats save tests
pytest test_auction_api.py                     # auction API tests
pytest test_trend_debug.py                     # trend debug tests
python test_trend_api.py                       # direct execution (if async main)

# Run specific test functions
pytest test_module.py::test_function           # single test
pytest test_module.py -k "test_specific"      # matching tests
pytest -v                                       # verbose output
pytest -s                                       # show print statements

# Database utilities
python verify_db.py                             # verify database integrity
python add_test_data.py                         # add test data

# Database initialization (after schema changes)
python -c "from data.database import init_database; init_database()"

# Quick start scripts
# Windows: run.bat
# Linux/Mac: chmod +x run.sh && ./run.sh
```

## Code Style Guidelines

### File Organization
- `src/main.py`: FastAPI routes + 交易日逻辑; `src/db_operations.py`: DB/topic/limit 管理
- `src/data_acquisition.py`: AkShare 采集; `src/trend_analysis.py`: 趋势评分（独立模块）
- `data/database.py`: schema/migrations（改后必须 `init_database()`）
- `templates/dashboard.html`: 前端一体文件; `config/`: JSON 配置; 测试 `test_*.py` 在根目录

### Imports, Formatting & Naming
- Standard → third-party → local; `src/` 顶部加 `sys.path.append(os.path.dirname(os.path.abspath(__file__)))`
- 4 空格缩进（禁 tab）, 120 char 行宽; 类 PascalCase / 函数 snake_case / 常量 UPPER_SNAKE_CASE
- Type hints 必填; API endpoint kebab-case（`/api/limit-stats`）; HTML ID/class kebab-case

### Error Handling & Logging
- 特定异常 + `logger.error(f"msg: {e}", exc_info=True)`; 回退方法 `_get_fallback_*`
- API 错误返回含 `"error"` 键的 dict 或 HTTPException
- 不记录敏感信息（API key/密码）; 详细调试用 `logging.debug()`

### Async/Await
- 数据采集方法 `async def`, main.py 路由用 async handler, 一律 `await`
- 异步测试 `asyncio.run(test_function())`

### Database Operations
- SQLite `data/fupan.db` 双表: `*_tmp`（盘中）/ 无后缀正式表（盘后）
- **CRITICAL**: 盘后自动建 topic 前必须先查正式表数据（保护用户手动操作）
- 盘中用 tmp 表（刷新前清空, 全量替换）; 盘后用正式表, 清 tmp, 仅无数据时自动建
- **Dual-table queries**: 数据可能在正式或 tmp 表 → 先查正式表, 无则查 tmp 兜底
- **Dual-table writes**: 盘中写正式 + tmp 双份
- 参数化查询 `cursor.execute('... WHERE id = ?', (id,))`; 用上下文管理器关闭连接
- schema 改动后跑 `python -c "from data.database import init_database; init_database()"`

### Trading Session Logic
- 交易时段 9:25-15:00（`is_in_trading_hours()`）
- 盘中 → tmp 表, 总是自动建 topic; 盘后/非交易日 → 正式表, 仅无数据时建
- `get_query_trading_date()`: 查询用（first_limits / topic_activations / first_limit_topics 关联）
- `get_display_trade_date()`: UI 显示用（limit_stats / continuous_limits / market_summary）

### Trend Stock Analysis (src/trend_analysis.py)
- 独立模块 100 分制: MA(25) + Gain60d(20) + MA20斜率(15) + 板块(10) + 量能(10) + 回撤(10)
- API 限流: 每只 2s 延迟, 连续失败 3 次终止
- 落库全部评分字段（ma_score/gain_60d_score/volume_score/recent_score/sector_score/drawdown_score）; `stock_daily_data` 存 K线 + `trend_stocks` 存评分

### Frontend/JavaScript
- camelCase 变量/函数, PascalCase 类; const/let 优先, 缩小全局作用域
- 浮动下拉 `z-index > 9999`
- 用 innerHTML（非 outerHTML）保事件绑定; 生产删 console.log
- 需要时 `event.preventDefault()` + `event.stopPropagation()`

### Configuration & HTML
- 配置存 `config/*.json`（入 git）; 后端 GET/POST API CRUD; 前端从 API 加载不用 localStorage
- 所有 `<div>` 正确闭合/嵌套; Modal tab 同层级, 内容 tab 默认 `style="display: none;"`

### CSS Development (from .rules/frontend-development.md)
- **CRITICAL**: 不定义重复 CSS class — 加样式前先搜
- 所有 style 块 `{ ... }` 配对完整; class 名带前缀（`.topic-card-`, `.help-modal-`）
- 提交前查 CSS 语法

### File Cleanup Safety
- [2026-10-19] **CRITICAL**: 删/移任何文件前 grep 引用 — PowerShell `Select-String -Path "src\*.py","*.py","*.html" -Pattern "filename"` / bash `grep -r "filename" src/ data/ *.py templates/`
- **`.env.1` MUST be preserved** — 避免 Bun auto-load（见 `BUN_ENV_ISSUE.md`）, 不改名/不移 `_to_delete/`; Mairui License 从它加载, 缺失则静默跳过 跌停/炸板/强势股（无报错, 结果为空）
  - 引用: `src/data_acquisition.py:21`（`load_dotenv('.env.1')`）, `src/main.py:3999`（`env_file = '.env.1'`）; 所有 `.env*` 在 `.gitignore`
- 清 `*.py` 前 `grep -r "import.*module_name"` 确认无引用
- 删除先移 `_to_delete/` 观察并测过后手动删; 禁 `rm -rf`

### Backup Policy (自 2026-07-15 起)
- **代码 / 配置 / 模板 / 前端 / 文档**: 全走 git（commit + push 到 GitHub）— `src/`、`templates/`、`static/`、`config/`、`data/database.py`、根目录脚本/配置、`.rules/`、AGENTS/README、`.gitignore`、`.env.example`
- **运行时数据** (`data/fupan.db`): `backup/backup_project.py` 镜像到 `D:\AI\my_programs\backup_YYYYMMDD_HHMMSS\`（~20MB 不适合 git, 公开数据镜像足够, 建议每周一次）
- **不入 git**: `backup/`、`_to_delete/`、`pencil*.pen`、`.mindscape/`、`.omo/`、`.anything-graph/`、`.opencodeignore*`、`data/cache/`、`data/*.json`、`data/*.xlsx`/`*.docx`、所有 `.env*`
- `backup/backup_project.py` 留观察期暂不删, 验证 git 备份足够后再决定废弃

### Testing & Docstrings
- 测试文件根目录 `test_*.py`, 描述性命名; 同时测 happy path 和错误场景
- 可 pytest 跑或直接跑（async main）
- UI 字符串中文, 代码/注释英文; 注释简洁, 复杂函数含 Args/Returns

## Tech Stack
FastAPI, SQLite, AkShare, Jinja2, Chart.js, async/await, pandas, numpy, aiohttp
- [2026-10-09] AG 导入命中 node-file-path 去重时，去掉 file_path 再新增可规避被跳过
- [2026-10-09] 图谱新增节点/边后补全子图实体与关联边，确保 webui 分区与关系完整
- [2026-10-09] 前端微调按钮位置时，先精确 Read 定位目标行再 Edit，避免误改周边结构
- [2026-10-09] .gitignore 须纳入临时/损坏备份目录，防止误推送大体积无用文件

<!-- OCM:START (scope=project) (do not edit this block manually) -->
## ⚠️ 可用项目经验 (7 条 — 任务开始前必查)

匹配当前任务的必须 `experience_get(id=<id>)` 读取完整内容再动手。不读 = 违反流程。

- **重建 DB 后系统性比对代码引用表 vs 实际表** `id=c58bf217-c251-4895-bf56-9d1975aca137` (project / negative)
  使用时机: 恢复 git 仓库后重建 DB / 排查 no such table / 任何 DB schema 恢复任务开始前。不读代价: 只修眼前报错, 漏掉一批同样缺失的表, 业务逐个炸。
  关键词: no such table, database.py, init_database, schema
- **first_limit_id→stock_id 迁移残留检查清单** `id=d5ca2670-9fd7-40ed-814a-0d33d8985db3` (project / negative)
  使用时机: 遇到 "no such column: first_limit_id" 报错, 或修改任何引用 first_limit_topics / topic_stock_relations 的 SQL 之前。不读代价: 漏改残留引用, 盘后路径继续崩。
  关键词: no such column: first_limit_id, first_limit_topics, topic_stock_relations
- **git 的 database.py schema 可能与生产 DB 脱节, 恢复后必须对齐代码** `id=ea7df2bd-bc56-4045-82cc-398f9c45b773` (project / negative)
  使用时机: 任何从 git 恢复/重建数据库后, 或遇到 "no such column" 报错时
  关键词: no such column, init_database, 重建数据库, schema 不一致
- **双表架构中查"历史日"必须用 first_limits 正式表,不能用 first_limits_tmp** `id=e8f9c81a-2e41-45e3-ace8-391212eacd99` (project / dev)
  使用时机: 修改/添加任何"按历史日期查询 first_limits" 的代码路径 (查昨日票/查前几日票) 之前. 包括 get_first_limits_by_date 调用、快照保存路径、盘中 GET 端点盘中分支. 不读代价: 查询走 first_limits_tmp 默认表 → tmp 只有当日数据 → 返回 0 行 → 前端显示空 / 显示 -- / 落库静默失败.
- **Minimal-Edit UI Refactor 模式** `id=2c36b0fd-30c2-491f-b14e-3fbdfe1921e9` (project / dev)
  使用时机: 用户偏好: UI 改版时严格遵守"最小代码修改"——只改外观, 不重写已验证的逻辑函数. 切换控件样式时优先用 CSS class + 结构替换, 不动 onchange 处理器和函数体.
- **盘后溢价值快照优先复用涨停池数据,避免调不稳定的实时行情API** `id=1d0d888a-e09e-42bd-8e8c-666a59b7108e` (project / dev)
  使用时机: 在 fetch_and_save_limit_data 已走通后,又调 fetch_spot_data / stock_zh_a_spot_em 等全市场实时行情 API 来补充涨跌幅的场景
不读代价: 非交易时段 stock_zh_a_spot_em 被服务器拒绝连接(RemoteDisconnected),快照保存静默失败(saved_count=0),用户无感知,前端永远拿不到数据
强制: experience_get + before 任何「盘后保存额外数据需要全市场涨跌幅」的设计。优先复用已获取的 limit_pool 数据,stock_zh_a_spot_em 作为降级兜底
- **溢价模式卡片重渲染必须用稳定的title选择器** `id=b76d9018-2b5c-4a52-af7d-4b5873938e14` (project / dev)
  使用时机: <parameter name="description">触发: 实现 togglePremiumMode / renderTodayFirstLimits / 类似「切换模式后局部刷新卡片」的场景
不读代价: 用 card.innerHTML.includes('🆕 今日首板') 等emoji+文字匹配时，标题会随模式切换变化（🆕 → 📈），第二次切换时匹配失败，DOM 不更新，用户看到「切换无反应」的假 bug
强制: 重渲染函数必须用 querySelector('#firstLimitsTitle') 或 cardTitle 容器的 textContent.includes('今日首板|昨日溢价') 等稳定文本匹配，不要用包含 emoji 的整段 HTML 字符串

<!-- OCM:END -->
- [2026-10-28] contenteditable 读取保留 display:none 需 clone 后手转 br，innerText 会吞隐藏文本
- [2026-10-28] textarea 改 contenteditable div 须显式补 overflow:auto 与 text-align:left
- [2026-10-28] 标签统一宽度用canvas measureText精确测量文本，字符数估算会因字体差异导致宽度不齐
- [2026-10-28] 首板sector字段实为申万二级行业且被数据源截断为4字，一级分类须靠静态映射表转换
