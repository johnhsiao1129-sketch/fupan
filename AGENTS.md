# AGENTS.md

This file contains conventions and commands for AI agents operating in this repository.

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
- `src/main.py`: FastAPI routes, service layer, trading date logic
- `src/db_operations.py`: Database operations, topic/limit management
- `src/data_acquisition.py`: Stock data fetching from AkShare API
- `src/trend_analysis.py`: Trend stock analysis module (independent, 100-point scoring)
- `data/database.py`: Database schema/migrations (run init_database() after changes)
- `templates/dashboard.html`: Main frontend (HTML/CSS/JS in one file)
- `static/`: Static JS/CSS assets
- `config/`: JSON configuration files
- Test files: root directory with `test_` prefix

### Imports, Formatting & Naming
- Standard → third-party → local imports
- Add `sys.path.append(os.path.dirname(os.path.abspath(__file__)))` at top of src/ files
- Use `from module import specific_class` over `import module`
- Example:
  ```python
  import asyncio
  from typing import List, Dict, Optional
  from fastapi import FastAPI
  from db_operations import RotationAnalysisDB
  ```
- 4 spaces indentation (no tabs), 120 char max line length
- Classes: PascalCase (`StockDataService`), Functions: snake_case (`get_limit_stats`)
- Private: `_load_history_data`, Constants: UPPER_SNAKE_CASE (`DB_PATH`, `MAX_RETRIES`)
- API endpoints: kebab-case (`/api/limit-stats`)
- HTML IDs/classes: kebab-case (`topic-modal`, `delete-modal-btn`)
- Type hints required: `def get_data(key: str) -> Optional[Dict]:`

### Error Handling & Logging
- Specific exceptions in try-except, log with `logger.error(f"msg: {e}", exc_info=True)`
- Fallback methods: `_get_fallback_*`
- API errors: return dicts with "error" key or use HTTPException
- Get logger: `logger = logging.getLogger(__name__)`
- Log levels: INFO, WARNING, ERROR
- Use f-strings: `logger.info(f"成功获取: {len(data)}条")`
- Never log sensitive info (API keys, passwords)
- Use logging.debug() for detailed development information

### Async/Await
- Data fetching methods: `async def`, routes in main.py use async handlers
- Always `await` async functions
- Run async tests: `asyncio.run(test_function())`

### Database Operations
- SQLite at `data/fupan.db` with temp tables: `*_tmp` (trading), formal tables without suffix (final)
- Always close connections (use context managers when possible)
- Parameterized queries: `cursor.execute('SELECT * FROM t WHERE id = ?', (id,))`
- **CRITICAL**: Check formal table data before auto-creating topics in post-market (protect user manual operations)
- Trading: use temp tables, clear before each refresh, full replacement
- Post-market: use formal tables, clear temp tables, auto-create only if no existing data
- Use `INSERT OR REPLACE` with UNIQUE constraints → update dependent tables to prevent orphaned references
- After schema changes in `database.py`, run: `python -c "from data.database import init_database; init_database()"`
- **Dual-table queries**: When data may be in either formal or temp tables (user operations during trading):
  - Query formal table first, then temp table as fallback
- **Dual-table writes**: During trading hours, write to both formal and temp tables

### Trading Session Logic
- Trading hours: 9:25-15:00 on trading days (check with `is_in_trading_hours()`)
- Trading → temp tables, auto-create topics always
- Post-market/non-trading → formal tables, auto-create only if no existing data
- `get_query_trading_date()`: for queries (first_limits, topic_activations, first_limit_topics associations)
- `get_display_trade_date()`: for UI display (limit_stats, continuous_limits, market_summary)

### Trend Stock Analysis (src/trend_analysis.py)
- Independent module with 100-point scoring system
- Categories: MA(25), Gain60d(20), MA20 Slope(15), Sector(10), Volume(10), Drawdown(10)
- API rate limiting: 2-second delay between stock fetches, max 3 consecutive failures
- Saves all scoring fields: ma_score, gain_60d_score, volume_score, recent_score, sector_score, drawdown_score
- Data persistence: stock_daily_data table for K-line, trend_stocks table for scoring results

### Frontend/JavaScript
- camelCase variables/functions, PascalCase classes
- Use const/let over var, minimize global scope
- z-index > 9999 for dropdowns that need to float
- Use innerHTML (not outerHTML) to preserve event bindings
- Prefer getElementById over querySelector for performance
- Remove console.log from production
- Use event.preventDefault() and event.stopPropagation()

### Configuration & HTML
- Store configs in `config/` as JSON files (committed to git, not in .gitignore)
- Backend provides GET/POST APIs for config CRUD (e.g., `/api/trend-filter-rules`)
- Frontend loads from APIs, not localStorage
- Ensure all `<div>` properly closed/nested
- Modal tabs at same nesting level, content tabs: `style="display: none;"` by default

### CSS Development (from .rules/frontend-development.md)
- **CRITICAL**: Never define duplicate CSS class names - search before adding new styles
- Ensure all style blocks have complete `{ ... }` pairs
- Use descriptive class names with prefixes (e.g., `.topic-card-`, `.help-modal-`)
- Check for broken CSS syntax before committing

### File Cleanup Safety
- **CRITICAL**: Before deleting/moving ANY file (including .env, .env.*, .json, .yaml, .toml), grep the codebase for references:
  ```bash
  # PowerShell
  Select-String -Path "src\*.py", "*.py", "*.html" -Pattern "filename"
  # Bash
  grep -r "filename" src/ data/ *.py templates/
  ```
- **`.env.1` MUST be preserved** — this project intentionally uses `.env.1` filename to avoid Bun auto-loading (see `BUN_ENV_ISSUE.md`). Do NOT rename to `.env` or move to `_to_delete/`.
  - Code references: `src/data_acquisition.py:21` (`load_dotenv('.env.1')`), `src/main.py:3999` (`env_file = '.env.1'`)
  - Mairui License is loaded from `.env.1`; missing it silently skips 跌停/炸板/强势股 data fetching (no error thrown, just empty results)
- All `.env*` files should be in `.gitignore` (currently: `.env`, `.env.1`, `.env.local`)
- When cleaning up `*.py` scripts: confirm with `grep -r "import.*module_name"` that no other code imports it
- Backup strategy: move candidates to `_to_delete/` first, test the app, then manually delete. Do NOT use `rm -rf` directly.

### Backup Policy (自 2026-07-15 起)
- **代码 / 配置 / 模板 / 前端 / 文档**: 全部走 `git` (commit + push 到 GitHub)
  - 包括: `src/`、`templates/`、`static/`、`config/`、`data/database.py`、根目录脚本/配置、`.rules/`、AGENTS/README、`.gitignore`、`.env.example`
- **运行时数据** (`data/fupan.db`): 由 `backup/backup_project.py` 镜像备份到 `D:\AI\my_programs\backup_YYYYMMDD_HHMMSS\`
  - 数据库体积大 (~20MB) 不适合 git; 但内容是公开的 A 股数据, 镜像足够
  - 备份频率建议: 每周一次 (数据库非高频变化)
- **不入 git 的内容**:
  - `backup/` 目录本身 (本地镜像)
  - `_to_delete/` 目录 (废弃脚本观察区)
  - `pencil*.pen` 设计文件 (临时草稿)
  - `.mindscape/`、`.omo/`、`.anything-graph/`、`.opencodeignore*` (AI 内部状态)
  - `data/cache/`、`data/*.json`、`data/*.xlsx`/`*.docx` (运行时缓存/用户文件)
  - 所有 `.env*` (敏感凭据)
- **`backup/backup_project.py`**: 留观察期, 暂不删。验证 git 备份足够后, 再决定是否彻底废弃。

### Testing & Docstrings
- Test files: `test_*.py` in root with descriptive names
- Test both happy path and error scenarios
- Tests can be run with pytest or directly (if script has async main)
- Chinese for UI strings, English for code/comments
- Keep comments concise, complex functions include Args/Returns

## Tech Stack
FastAPI, SQLite, AkShare, Jinja2, Chart.js, async/await, pandas, numpy, aiohttp
