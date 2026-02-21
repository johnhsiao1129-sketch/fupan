# AGENTS.md

This file contains conventions and commands for AI agents operating in this repository.

## Build, Lint, and Test Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Start the development server (auto-reload on file changes)
python start.py

# Run all tests with pytest
python -m pytest

# Run a single test file directly (tests are in root directory)
python test.py
python test_limit_stats_db.py

# Run specific pytest tests with filtering
pytest test_module.py::test_function      # run single test
pytest test_module.py -k "test_specific"   # run matching tests
pytest -v                                   # verbose output
pytest -s                                   # show print statements

# Database utilities
python verify_db.py                         # verify database integrity
python add_test_data.py                     # add test data

# One-click start (Windows/Linux)
# Windows: run.bat
# Linux/Mac: chmod +x run.sh && ./run.sh
```

## Code Style Guidelines

### Imports
- Standard library imports first, then third-party imports, then local imports
- Use `from module import specific_class` over `import module`
- Add `sys.path.append(os.path.dirname(os.path.abspath(__file__)))` at top of src/ files
- Example:
  ```python
  import asyncio
  from typing import List, Dict, Optional
  from fastapi import FastAPI
  from db_operations import RotationAnalysisDB
  ```

### Formatting
- Use 4 spaces for indentation (no tabs)
- Maximum line length: 120 characters
- Align multi-line dictionaries/lists/parameters vertically
- Place docstrings immediately after function/class definitions

### Type Hints
- Use type hints for parameters and returns
- Import from `typing` module: `List`, `Dict`, `Optional`, `Tuple`, `Any`
- Use `->` to indicate return types: `def get_records(self) -> List[Dict]:`
- Use Optional for nullable returns: `def get_data(key: str) -> Optional[Dict]:`

### Naming Conventions
- Classes: PascalCase (`StockDataService`)
- Functions/Methods: snake_case (`get_limit_stats`)
- Private methods: `_load_history_data`
- Constants: UPPER_SNAKE_CASE (`DB_PATH`, `MAX_RETRIES`)
- API endpoints: kebab-case (`/api/limit-stats`)
- HTML IDs/classes: kebab-case (`topic-modal`, `delete-modal-btn`)

### Error Handling
- Use try-except blocks with specific exception types
- Log errors with `logger.error(f"message: {e}", exc_info=True)` for debugging
- Implement fallback methods prefixed with `_get_fallback_*`
- Return error dicts with "error" key for API responses
- Use HTTPException for FastAPI API errors with status codes

### Logging
- Get logger with `logger = logging.getLogger(__name__)`
- Log levels: INFO, WARNING, ERROR
- Use f-strings for messages: `logger.info(f"成功获取: {len(data)}条")`
- IMPORTANT: Avoid logging sensitive information (API keys, passwords)
- Remove console.log from production templates before final deployment
- Use logging.debug for detailed development information

### Async/Await
- Data fetching methods should be async (`async def`)
- Routes in main.py use async handlers
- Use `await` when calling async functions
- Run tests with `asyncio.run(test_function())` for async test functions

### Database Operations
- SQLite database at `data/fupan.db`
- Always close connections after use (use context managers when possible)
- Use parameterized queries to prevent SQL injection: `cursor.execute('SELECT * FROM t WHERE id = ?', (id,))`
- Commit transactions explicitly after updates, use rollback on errors
- Use `SELECT MAX(date)` to get latest dates, not ORDER BY DESC LIMIT 1
- CRITICAL: When using INSERT OR REPLACE on tables with UNIQUE constraints, update dependent tables to prevent orphaned references

### Frontend/JavaScript Guidelines
- Use camelCase for variables/functions (`getElementById`), PascalCase for classes
- Minimize global scope; use const and let instead of var
- Use z-index > 9999 for dropdowns that need to float over other elements
- Use innerHTML for content updates (not outerHTML) to preserve event bindings
- Use getElementById over querySelector when possible for performance
- Avoid global console.log statements in production code
- Use event.preventDefault() and event.stopPropagation() for event handling

### File Organization
- `src/main.py`: API routes + service layer, trading date logic
- `src/db_operations.py`: Database operations, topic/limit management
- `src/data_acquisition.py`: Stock data fetching from AkShare API
- `src/market_mood_calculator.py`: Market mood scoring algorithms
- `data/database.py`: Database schema/migrations
- `templates/dashboard.html`: Main frontend (HTML/CSS/JS in one file)
- `static/`: Static JS/CSS assets
- Test files: root directory with `test_` prefix

### Docstrings & Comments
- Use Chinese for user-facing strings, English for code/comments
- Keep comments concise; avoid obvious comments (e.g., `# increment i`)
- Docstrings for complex functions should include Args/Returns sections
- Use triple quotes for multi-line docstrings

### FastAPI Specific
- Use `@app.get()`, `@app.post()` decorators for routes
- Return data directly (FastAPI handles JSON serialization)
- Use `HTTPException` for API errors with status codes
- Enable CORS with `CORSMiddleware` for all origins
- Use Pydantic models for request/response validation when possible

### Data Visualization
- Chart.js for charts (loaded from /static/chart.umd.min.js)
- Flatpickr for date pickers
- ECharts for complex visualizations
- Use consistent color schemes across charts (#4ecdc4, #e74c3c, #f39c12)
- Stacked bar charts use 'stack' property in dataset configuration

### Testing
- Test files: `test_*.py` in root directory
- Test functions start with `test_`
- Use descriptive names: `test_get_limit_stats_by_date`
- Test both happy path and error scenarios
- Use pytest's fixture system for shared test data

### Project-Specific Guidelines
- Trading day handling: `get_query_trading_date()` for active trading day, `get_display_trade_date()` for UI
- Topic management: topic_activations for daily state, topic_stock_relations for persistent relations
- First limit topics: Use `stock_id + association_date` for references (NOT first_limit_id)
- Data refresh: After INSERT OR REPLACE on first_limits, update first_limit_topics to prevent orphaned references
- Modal tracking: Use `modalSource` variable to distinguish modal sources ('today_first_limits' vs 'rotation')
- Topic deletion: Always check topic_stock_relations table before deleting, not just first_limit_topics

This project uses FastAPI, SQLite, AkShare, Jinja2, Chart.js, and async operations.
