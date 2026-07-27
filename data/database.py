import sqlite3
from datetime import datetime, timedelta
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# DB_PATH 用绝对路径, 避免被 CWD 影响
# (旧版用 "data/fupan.db" 相对路径, 在 CWD 不是项目根时会创建空 DB 到错误位置)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = str(_PROJECT_ROOT / "data" / "fupan.db")

MARKET_MOOD_MAP = {
    1: "低迷",
    2: "谨慎",
    3: "正常",
    4: "活跃",
    5: "狂热"
}

def init_database():
    """初始化数据库，创建所有表"""
    os.makedirs("data", exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 迁移 first_limit_topics 表的 UNIQUE 约束（如果已存在但约束不对）
    cursor.execute('''
        SELECT sql FROM sqlite_master WHERE type='table' AND name='first_limit_topics'
    ''')
    result = cursor.fetchone()
    migrate_first_limit_topics_data = None

    if result and result[0]:
        table_sql = result[0]
        # 检查是否包含旧的 UNIQUE 约束（不包含 association_date）
        if 'UNIQUE(first_limit_id, topic_id),' in table_sql or 'UNIQUE(first_limit_id, topic_id)\n' in table_sql:
            logger.info("发现旧版本的 first_limit_topics 表结构，开始迁移...")
            # 获取现有数据
            cursor.execute('SELECT first_limit_id, topic_id, create_time FROM first_limit_topics')
            existing_data = cursor.fetchall()

            # 删除旧表
            cursor.execute('DROP TABLE IF EXISTS first_limit_topics')
            conn.commit()

            logger.info(f"旧表已删除，备份了 {len(existing_data)} 条记录")

            # 保存数据用于迁移（会在创建新表后恢复）
            migrate_first_limit_topics_data = existing_data
    
    # 1. 题材表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS topics (
            topic_id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_name TEXT UNIQUE NOT NULL,
            description TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(topic_name)
        )
    ''')
    
    # 2. 个股表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stocks (
            stock_id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT UNIQUE NOT NULL,
            stock_name TEXT,
            industry TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    ''')
    
    # 3. 题材轮动活跃表（核心表）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rotation_actives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            content TEXT,
            is_active INTEGER DEFAULT 1,
            timestamp TEXT NOT NULL,
            stage TEXT,
            UNIQUE(topic_id, date),
            FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        SELECT sql FROM sqlite_master
        WHERE type='table' AND name='rotation_actives'
    ''')
    result = cursor.fetchone()
    if result and result[0]:
        if 'stage TEXT' not in result[0]:
            cursor.execute('ALTER TABLE rotation_actives ADD COLUMN stage TEXT')
            logger.info("已为 rotation_actives 表添加 stage 字段")

    # 4. 题材-个股关联表（多对多关系）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS topic_stock_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            stock_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            create_time TEXT NOT NULL,
            UNIQUE(topic_id, stock_id, date, relation_type),
            FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE CASCADE,
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')
    
    # 5. 首板记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS first_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            limit_date TEXT NOT NULL,
            limit_time TEXT,
            limit_price REAL,
            open_price REAL,
            amount REAL,
            reason TEXT,
            source TEXT,
            create_time TEXT NOT NULL,
            UNIQUE(stock_id, limit_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')
    
    # 6. 首板-题材关联表
    # 核心业务逻辑：
    # - create_time: 用户操作时间（如周日复盘周五数据，创建时间记录为周日）
    # - association_date: 首板与题材的真实活跃交易日（如周五的首板，记录为周五）
    # - UNIQUE(stock_id, topic_id, association_date): 同一股票在同一日期只能关联一个题材（支持复盘历史）
    # - 修改说明：将 first_limit_id 改为 stock_id，避免因数据刷新导致ID变化带来的关联失效问题
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS first_limit_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL,
            create_time TEXT NOT NULL,
            association_date TEXT,
            UNIQUE(stock_id, topic_id, association_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE,
            FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE CASCADE
        )
    ''')

    # 恢复 first_limit_topics 旧数据（如果需要迁移）
    if migrate_first_limit_topics_data:
        logger.info("开始恢复 first_limit_topics 旧数据...")
        for first_limit_id, topic_id, create_time in migrate_first_limit_topics_data:
            # 从 first_limits 表查询 limit_date，作为 association_date
            cursor.execute('SELECT limit_date FROM first_limits WHERE id = ?', (first_limit_id,))
            limit_date_result = cursor.fetchone()

            if limit_date_result and limit_date_result[0]:
                association_date = limit_date_result[0]
                # 插入数据（使用首板的原日期作为 association_date）
                try:
                    cursor.execute('''
                        INSERT INTO first_limit_topics (first_limit_id, topic_id, create_time, association_date)
                        VALUES (?, ?, ?, ?)
                    ''', (first_limit_id, topic_id, create_time, association_date))
                except Exception as e:
                    logger.warning(f"恢复数据失败（可能重复）: first_limit_id={first_limit_id}, topic_id={topic_id}, error={e}")
            else:
                logger.warning(f"无法找到首板记录的日期: first_limit_id={first_limit_id}")

        conn.commit()
        logger.info(f"恢复 first_limit_topics 旧数据完成")
    
    # 7. 涨跌停统计表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS limit_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL UNIQUE,
            first_limit INTEGER NOT NULL,
            continuous_limit INTEGER NOT NULL,
            exploded INTEGER NOT NULL,
            limit_down INTEGER NOT NULL,
            explode_rate REAL,
            market_mood INTEGER NOT NULL DEFAULT 3,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL
        )
    ''')
    
    # 8. 涨跌停分析说明表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS limit_stats_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL UNIQUE,
            analysis TEXT,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL
        )
    ''')

    # 9. 交易日表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trading_days (
            date TEXT PRIMARY KEY,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')

    # 10. 跌停股票表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS limit_down (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            price REAL,
            change_percent REAL,
            amount REAL,
            reason TEXT,
            source TEXT DEFAULT 'mairui',
            create_time TEXT NOT NULL,
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')

    # 11. 炸板股票表
    # explode_type字段说明：
    # - 'limit_with_explode': 涨停股池中炸过板的票（hslt/ztgc接口，最终涨停）
    # - 'pure_explode': 炸板且最终未涨停的票（hslt/zbgc接口，未涨停）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exploded (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            limit_price REAL,
            first_limit_time TEXT,
            exploded_count INTEGER NOT NULL,
            continuous_days INTEGER,
            amount REAL,
            sector TEXT,
            reason TEXT,
            source TEXT DEFAULT 'mairui',
            explode_type TEXT NOT NULL,
            create_time TEXT NOT NULL,
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')

    # ========== 索引 ==========
    
    # topics 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_topics_name ON topics(topic_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_topics_active ON topics(is_active)')
    
    # stocks 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_stocks_code ON stocks(stock_code)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_stocks_name ON stocks(stock_name)')

    # rotation_actives 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_actives_topic_date ON rotation_actives(topic_id, date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_actives_date ON rotation_actives(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_actives_active ON rotation_actives(is_active)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_actives_topic_date_active ON rotation_actives(topic_id, date, is_active)')

    # topic_stock_relations 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_relations_topic_date ON topic_stock_relations(topic_id, date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_relations_stock_date ON topic_stock_relations(stock_id, date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_relations_type ON topic_stock_relations(relation_type)')
    
    # first_limits 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limits_stock_date ON first_limits(stock_id, limit_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limits_date ON first_limits(limit_date)')
    
    # first_limit_topics 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limit_topics_limit ON first_limit_topics(first_limit_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limit_topics_topic ON first_limit_topics(topic_id)')
    
    # limit_stats 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limit_stats_trade_date ON limit_stats(trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limit_stats_mood ON limit_stats(market_mood)')
    
    # limit_stats_analysis 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limit_analysis_trade_date ON limit_stats_analysis(trade_date)')

    # limit_down 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limit_down_date ON limit_down(trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limit_down_stock ON limit_down(stock_id)')

    # exploded 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_exploded_date ON exploded(trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_exploded_stock ON exploded(stock_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_exploded_count ON exploded(exploded_count)')

    # ========== 数据库迁移：添加 explode_type 字段到 exploded 表 ==========
    # 检查 explode_type 字段是否存在
    cursor.execute("PRAGMA table_info(exploded)")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]
    if 'explode_type' not in column_names:
        cursor.execute('ALTER TABLE exploded ADD COLUMN explode_type TEXT NOT NULL DEFAULT "pure_explode"')
        print("已添加 explode_type 字段到 exploded 表")

    # 10. 市场整体状态总结表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS market_status_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL UNIQUE,
            summary_content TEXT NOT NULL,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL
        )
    ''')

    # 11. 连板梯队分析表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS continuous_limits_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL UNIQUE,
            analysis TEXT NOT NULL,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL
        )
    ''')

    # 12. 连板梯队历史数据表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS continuous_limits_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            price REAL,
            first_time TEXT NOT NULL,
            continuous_days INTEGER NOT NULL,
            sector TEXT,
            reason TEXT,
            amount REAL,
            create_time TEXT NOT NULL,
            UNIQUE(trade_date, code)
        )
    ''')

    # 13. 人气榜数据源表（选项卡管理）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS popularity_sources (
            source_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT UNIQUE NOT NULL,
            description TEXT,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    ''')

    # 14. 人气榜标的记录表
    # 业务规则：同一交易日、同一数据源、同一排名只能有一个标的
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS popularity_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            rank INTEGER NOT NULL,
            price REAL,
            change_percent REAL,
            amount REAL,
            sector TEXT,
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(source_id, trade_date, rank),
            FOREIGN KEY (source_id) REFERENCES popularity_sources(source_id) ON DELETE CASCADE,
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')

    # 15. 成交额榜类型表（竞价/全天）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS amount_types (
            type_id INTEGER PRIMARY KEY AUTOINCREMENT,
            type_name TEXT UNIQUE NOT NULL,
            description TEXT,
            query_time TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    ''')

    # 16. 成交额榜标的记录表
    # 业务规则：
    # - 竞价数据：每天9:25查询录入，之后不可修改
    # - 全天数据：每天15:00查询录入，之后不可修改
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS amount_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type_id INTEGER NOT NULL,
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            rank INTEGER NOT NULL,
            price REAL,
            change_percent REAL,
            amount REAL,
            sector TEXT,
            reason TEXT,
            is_final INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(type_id, trade_date, rank),
            FOREIGN KEY (type_id) REFERENCES amount_types(type_id) ON DELETE CASCADE,
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')

    # 17. 强势股数据表
    # 业务规则：每天获取强势股池数据并存储，按热度类型分类
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS strong_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            hot_type TEXT NOT NULL,
            rank INTEGER,
            price REAL,
            change_percent REAL,
            amount REAL,
            turnover_rate REAL,
            volume_ratio REAL,
            is_new_high INTEGER DEFAULT 0,
            continuous_limit_days INTEGER DEFAULT 0,
            sector TEXT,
            reason TEXT,
            source TEXT DEFAULT 'mairui',
            created_at TEXT NOT NULL,
            UNIQUE(stock_id, trade_date, hot_type),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')

    # 18. 强势股热度类型表（用于管理不同的热度分类）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS strong_stock_types (
            type_id INTEGER PRIMARY KEY AUTOINCREMENT,
            type_name TEXT UNIQUE NOT NULL,
            description TEXT,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    ''')

     # 19. 趋势票表（存储趋势票分析结果）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trend_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            total_score INTEGER NOT NULL,
            ma_score INTEGER NOT NULL,
            gain_60d_score INTEGER NOT NULL,
            volume_score INTEGER NOT NULL,
            recent_score INTEGER NOT NULL,
            ma60_score INTEGER NOT NULL,
            sector_score INTEGER NOT NULL,  -- 题材强度得分
            drawdown_score INTEGER NOT NULL,  -- 回撤控制得分
            -- 趋势特征
            ma5 REAL,
            ma10 REAL,
            ma20 REAL,
            ma60 REAL,
            change_pct_60d REAL,
            drawdown_20d REAL,
            volume_ratio REAL,
            -- 分类
            trend_level TEXT,  -- S/A/B/C
            is_approximate INTEGER DEFAULT 0,  -- 0=精确分析, 1=初选(未验证)
            created_at TEXT NOT NULL,
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')

    # 迁移：为已存在的 trend_stocks 添加新字段
    cursor.execute("PRAGMA table_info(trend_stocks)")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]

    if 'is_approximate' not in column_names:
        cursor.execute('ALTER TABLE trend_stocks ADD COLUMN is_approximate INTEGER DEFAULT 0')
        print("已添加 is_approximate 字段到 trend_stocks 表")

    if 'sector_score' not in column_names:
        cursor.execute('ALTER TABLE trend_stocks ADD COLUMN sector_score INTEGER DEFAULT 0')
        cursor.execute('UPDATE trend_stocks SET sector_score = 0 WHERE sector_score IS NULL')
        print("已添加 sector_score 字段到 trend_stocks 表")

    if 'drawdown_score' not in column_names:
        cursor.execute('ALTER TABLE trend_stocks ADD COLUMN drawdown_score INTEGER DEFAULT 0')
        cursor.execute('UPDATE trend_stocks SET drawdown_score = 0 WHERE drawdown_score IS NULL')
        print("已添加 drawdown_score 字段到 trend_stocks 表")

    # 20. 股票日K线数据表（存储趋势票的K线数据）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_daily_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            amount REAL,
            change_pct REAL,
            turnover REAL,
            ma5 REAL,
            ma10 REAL,
            ma20 REAL,
            ma60 REAL,
            volume_ratio REAL,
            change_pct_60d REAL,
            drawdown_20d REAL,
            created_at TEXT NOT NULL,
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')

    # ========== 新表索引 ==========

    # market_status_summary 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_market_status_summary_trade_date ON market_status_summary(trade_date)')

    # continuous_limits_analysis 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_continuous_limits_analysis_trade_date ON continuous_limits_analysis(trade_date)')

    # continuous_limits_history 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_continuous_limits_history_trade_date ON continuous_limits_history(trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_continuous_limits_history_code ON continuous_limits_history(code)')

    # strong_stocks 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_strong_stocks_date ON strong_stocks(trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_strong_stocks_stock ON strong_stocks(stock_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_strong_stocks_type ON strong_stocks(hot_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_strong_stocks_date_type ON strong_stocks(trade_date, hot_type)')

    # strong_stock_types 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_strong_types_name ON strong_stock_types(type_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_strong_types_active ON strong_stock_types(is_active)')

    # trend_stocks 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trend_stocks_date ON trend_stocks(trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trend_stocks_stock ON trend_stocks(stock_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trend_stocks_score ON trend_stocks(total_score)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trend_stocks_date_score ON trend_stocks(trade_date, total_score)')

    # stock_daily_data 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_daily_data_stock_date ON stock_daily_data(stock_id, trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_daily_data_date ON stock_daily_data(trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_daily_data_stock ON stock_daily_data(stock_id)')

    # 22. 首板溢价快照表（存储首板标的在后续交易日的涨跌幅）
    # 业务规则：
    # - premium_date: 溢价快照日期（观察日）
    # - limit_date: 首板日期
    # - change_percent: 该标的在premium_date的涨跌幅
    # - 唯一约束: 同一股票在同一观察日对同一首板日只能有一条记录
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS first_limit_premiums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            premium_date TEXT NOT NULL,
            limit_date TEXT NOT NULL,
            change_percent REAL,
            snapshot_time TEXT,
            create_time TEXT NOT NULL,
            UNIQUE(stock_id, limit_date, premium_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')

    # first_limit_premiums 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_premiums_limit_date ON first_limit_premiums(limit_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_premiums_premium_date ON first_limit_premiums(premium_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_premiums_stock_limit ON first_limit_premiums(stock_id, limit_date)')

    # 23. 盘中记录表（手动记录盘面信息，每条带 HH:MM 时间标签）
    # 业务规则：
    # - trade_date + note_time 唯一：同一时间同一日期只一条
    # - content 多段落用 \n\n 分隔
    # - is_manual_time: 1=用户手选时间, 0=系统自动时间
    # - created_at: 首次创建时刻（90s 合并判断用）
    # - updated_at: 最后修改时刻
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS intraday_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            note_time TEXT NOT NULL,
            content TEXT NOT NULL,
            is_manual_time INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(trade_date, note_time)
        )
    ''')

    # intraday_notes 表索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_intraday_notes_date ON intraday_notes(trade_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_intraday_notes_date_time ON intraday_notes(trade_date, note_time)')

    # ========== 临时表（盘中数据） ==========
    # 20. 首板临时表（盘中数据）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS first_limits_tmp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            limit_date TEXT NOT NULL,
            first_limit_time TEXT,
            final_limit_time TEXT,
            limit_price REAL,
            open_price REAL,
            amount REAL,
            reason TEXT,
            source TEXT,
            create_time TEXT NOT NULL,
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
        )
    ''')

    # 21. 首板-题材临时关联表（盘中数据）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS first_limit_topics_tmp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            first_limit_id INTEGER,
            topic_id INTEGER NOT NULL,
            create_time TEXT NOT NULL,
            association_date TEXT,
            UNIQUE(stock_id, topic_id, association_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE,
            FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE CASCADE
        )
    ''')

    # 22. 题材临时激活表（盘中数据）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS topic_activations_tmp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            activation_date TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(topic_id, activation_date),
            FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE CASCADE
        )
    ''')

    # ========== 临时表索引 ==========
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limits_tmp_stock_date ON first_limits_tmp(stock_id, limit_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limits_tmp_date ON first_limits_tmp(limit_date)')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limit_topics_tmp_limit ON first_limit_topics_tmp(first_limit_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_limit_topics_tmp_topic ON first_limit_topics_tmp(topic_id)')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_activations_tmp_topic_date ON topic_activations_tmp(topic_id, activation_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_activations_tmp_date ON topic_activations_tmp(activation_date)')

    # ========== 修复 first_limits_tmp 表的 limit_type 默认值 ==========
    cursor.execute("UPDATE first_limits_tmp SET limit_type = '10%' WHERE limit_type IS NULL")
    updated_count = cursor.rowcount
    if updated_count > 0:
        print(f"✓ 已修复 first_limits_tmp 表的 limit_type 默认值: {updated_count} 条记录")

    conn.commit()
    conn.close()
    print(f"数据库初始化完成: {DB_PATH}")

def migrate_old_rotation_data():
    """迁移旧的 rotation_analyses 数据到新表结构"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 检查是否存在旧表
    cursor.execute('''
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='rotation_analyses'
    ''')
    old_table_exists = cursor.fetchone()
    
    if not old_table_exists:
        print("未找到旧表 rotation_analyses，跳过迁移")
        conn.close()
        return
    
    # 读取旧数据中的所有题材
    cursor.execute('SELECT DISTINCT topic FROM rotation_analyses')
    old_topics = cursor.fetchall()
    
    # 创建题材（如果不存在）
    created_topics = 0
    for (topic_name,) in old_topics:
        cursor.execute('''
            INSERT OR IGNORE INTO topics (topic_name, created_at, updated_at)
            VALUES (?, ?, ?)
        ''', (topic_name, datetime.now().isoformat(), datetime.now().isoformat()))
        if cursor.rowcount > 0:
            created_topics += 1
    
    print(f"创建了 {created_topics} 个新题材")
    
    # 迁移分析记录
    cursor.execute('''
        INSERT INTO rotation_actives (topic_id, day, content, date, timestamp, is_active)
        SELECT t.topic_id, ra.day, ra.content,
               CASE WHEN ra.date IS NULL OR ra.date = '' THEN
                    date('now', ra.day || ' days')
               ELSE ra.date END,
               ra.timestamp,
               CASE WHEN ra.content IS NULL OR ra.content = '' THEN 0 ELSE 1 END
        FROM rotation_analyses ra
        JOIN topics t ON ra.topic = t.topic_name
    ''')
    
    migrated_count = cursor.rowcount
    conn.commit()
    
    # 备份旧表（而不是删除）
    backup_table = 'rotation_analyses_backup'
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS {backup_table} AS 
        SELECT * FROM rotation_analyses
    ''')
    
    # 删除旧表
    cursor.execute('DROP TABLE IF EXISTS rotation_analyses')
    
    print(f"成功迁移 {migrated_count} 条记录")
    print(f"旧表已备份为: {backup_table}")
    
    conn.close()

def migrate_from_json():
    """从JSON文件迁移数据到数据库（备用方案）"""
    import json
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    json_file = "data/rotation_analysis.json"
    if os.path.exists(json_file):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        analyses = data.get('analyses', [])
        created_count = 0
        migrated_count = 0

        for analysis in analyses:
            try:
                topic_name = analysis.get('topic')
                content = analysis.get('content', '')
                date = analysis.get('date')
                timestamp = analysis.get('timestamp', datetime.now().isoformat())

                # 如果 date 为空，使用当前日期
                if not date:
                    date = datetime.now().strftime("%Y-%m-%d")

                # 创建题材
                cursor.execute('''
                    INSERT OR IGNORE INTO topics (topic_name, created_at, updated_at)
                    VALUES (?, ?, ?)
                ''', (topic_name, datetime.now().isoformat(), datetime.now().isoformat()))
                if cursor.rowcount > 0:
                    created_count += 1

                # 获取 topic_id
                cursor.execute('SELECT topic_id FROM topics WHERE topic_name = ?', (topic_name,))
                result = cursor.fetchone()
                topic_id = result[0] if result else None

                if topic_id:
                    # 插入分析记录
                    is_active = 1 if content and content.strip() else 0
                    cursor.execute('''
                        INSERT OR REPLACE INTO rotation_actives
                        (topic_id, content, date, timestamp, is_active)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (topic_id, content, date, timestamp, is_active))
                    migrated_count += 1

            except Exception as e:
                print(f"迁移记录失败: {analysis}, 错误: {e}")

        conn.commit()
        print(f"从JSON迁移: 创建 {created_count} 个题材, 迁移 {migrated_count} 条记录")

    conn.close()

def reset_database():
    """重置数据库（删除所有表并重新创建）"""
    import shutil
    
    if os.path.exists(DB_PATH):
        backup_path = DB_PATH.replace('.db', '_backup.db')
        shutil.copy(DB_PATH, backup_path)
        print(f"已备份数据库到: {backup_path}")
        os.remove(DB_PATH)
        print(f"已删除原数据库: {DB_PATH}")
    
    init_database()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'reset':
        reset_database()
    else:
        init_database()
        migrate_old_rotation_data()
        migrate_from_json()
