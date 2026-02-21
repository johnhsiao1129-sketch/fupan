"""
市场情绪计算系统 - 数据库迁移脚本

说明：
- 本脚本用于创建市场情绪计算系统所需的数据库表
- 执行前请确保已备份数据库文件
- 执行方法：python data/migration_market_mood.py
"""

import sqlite3
import sys
import os

# 数据库路径
DB_PATH = "data/fupan.db"


def create_tables():
    """
    创建市场情绪计算系统所需的所有表
    """
    print("=" * 60)
    print("开始创建市场情绪计算系统表结构...")
    print("=" * 60)
    
    if not os.path.exists(DB_PATH):
        print(f"❌ 错误：数据库文件不存在：{DB_PATH}")
        return False
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # ========== 表1：市场情绪积分规则配置表 ==========
        print("\n[1/7] 创建市场情绪积分规则配置表...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_mood_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                indicator_name TEXT NOT NULL,
                indicator_code TEXT NOT NULL UNIQUE,
                direction TEXT NOT NULL,
                weight REAL NOT NULL,
                baseline REAL DEFAULT NULL,
                calculation_rule TEXT,
                is_enabled INTEGER DEFAULT 1,
                create_time TEXT DEFAULT (datetime('now', 'localtime')),
                update_time TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        print("✓ 表 market_mood_config 创建成功")
        
        # ========== 表2：状态划分阈值配置表 ==========
        print("\n[2/7] 创建状态划分阈值配置表...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_mood_thresholds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mood_level INTEGER NOT NULL UNIQUE,
                mood_name TEXT NOT NULL,
                score_min REAL NOT NULL,
                score_max REAL NOT NULL,
                description TEXT,
                color_code TEXT,
                create_time TEXT DEFAULT (datetime('now', 'localtime')),
                update_time TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        print("✓ 表 market_mood_thresholds 创建成功")
        
        # ========== 表3：每日新高数据表 ==========
        print("\n[3/7] 创建每日新高数据表...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_highs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL UNIQUE,
                all_time_high_count INTEGER DEFAULT 0,
                one_year_high_count INTEGER DEFAULT 0,
                six_month_high_count INTEGER DEFAULT 0,
                all_time_high_list TEXT,
                one_year_high_list TEXT,
                six_month_high_list TEXT,
                median_all_time_high REAL DEFAULT 0,
                median_one_year_high REAL DEFAULT 0,
                median_six_month_high REAL DEFAULT 0,
                high_strength_index REAL DEFAULT 0,
                create_time TEXT DEFAULT (datetime('now', 'localtime')),
                update_time TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        print("✓ 表 daily_highs 创建成功")
        
        # ========== 表4：连板梯队详细信息表 ==========
        print("\n[4/7] 创建连板梯队详细信息表...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS continuous_limits_detail (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                tier_height INTEGER NOT NULL,
                stock_count INTEGER DEFAULT 0,
                stock_list TEXT,
                max_history_height INTEGER DEFAULT 0,
                ladder_completeness INTEGER DEFAULT 0,
                ladder_avg_count REAL DEFAULT 0,
                create_time TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(trade_date, tier_height)
            )
        ''')
        print("✓ 表 continuous_limits_detail 创建成功")
        
        # 创建索引提高查询性能
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_continuous_limits_trade_date 
            ON continuous_limits_detail(trade_date)
        ''')
        print("✓ 创建索引 continuous_limits_detail(trade_date)")
        
        # ========== 表5：题材延续性表 ==========
        print("\n[5/7] 创建题材延续性表...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS topic_continuity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                first_limit_count INTEGER DEFAULT 0,
                continuity_days INTEGER DEFAULT 1,
                tier_health_score INTEGER DEFAULT 0,
                is_hot_topic INTEGER DEFAULT 0,
                topic_stage TEXT DEFAULT 'startup',
                create_time TEXT DEFAULT (datetime('now', 'localtime')),
                update_time TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(trade_date, topic_name)
            )
        ''')
        print("✓ 表 topic_continuity 创建成功")
        
        # 创建索引
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_topic_continuity_trade_date 
            ON topic_continuity(trade_date)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_topic_continuity_topic_name 
            ON topic_continuity(topic_name)
        ''')
        print("✓ 创建索引 topic_continuity")
        
        # ========== 表6：每日低点数据表 ==========
        print("\n[6/7] 创建每日低点数据表...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_lows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL UNIQUE,
                limit_down_count INTEGER DEFAULT 0,
                continuous_limit_down_count INTEGER DEFAULT 0,
                count_2day INTEGER DEFAULT 0,
                count_3day INTEGER DEFAULT 0,
                count_4day INTEGER DEFAULT 0,
                count_5day_plus INTEGER DEFAULT 0,
                list TEXT,
                create_time TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        print("✓ 表 daily_lows 创建成功")
        
        # ========== 表7：大盘指数数据表 ==========
        print("\n[7/7] 创建大盘指数数据表...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL UNIQUE,
                index_name TEXT NOT NULL DEFAULT 'sh000001',
                open_price REAL,
                close_price REAL,
                high_price REAL,
                low_price REAL,
                change_percent REAL DEFAULT 0,
                volume REAL,
                amount REAL,
                create_time TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        print("✓ 表 market_index 创建成功")
        
        # ========== 表8：积分历史记录表 ==========
        print("\n[8/7] 创建积分历史记录表...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_mood_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL UNIQUE,
                total_score REAL,
                normalized_scores TEXT,
                final_scores TEXT,
                mood_level INTEGER,
                mood_name TEXT,
                indicator_details TEXT,
                create_time TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        print("✓ 表 market_mood_history 创建成功")
        
        # 提交事务
        conn.commit()
        
        print("\n" + "=" * 60)
        print("✅ 数据库表结构创建完成！")
        print("=" * 60)
        
        print("\n接下来的步骤：")
        print("1. 执行初始化配置脚本：data/init_market_mood_config.sql")
        print("2. 使用市场情绪计算器计算历史数据")
        
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ 错误：创建表结构失败 - {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        conn.close()


def check_existing_tables():
    """
    检查已存在的表
    """
    print("\n检查已存在的表...")
    
    if not os.path.exists(DB_PATH):
        print(f"数据库文件不存在：{DB_PATH}")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name IN (
            'market_mood_config', 'market_mood_thresholds', 
            'daily_highs', 'continuous_limits_detail',
            'topic_continuity', 'daily_lows', 'market_index',
            'market_mood_history'
        ) 
        ORDER BY name
    ''')
    
    tables = cursor.fetchall()
    conn.close()
    
    if tables:
        print(f"已存在 {len(tables)} 个表：")
        for table in tables:
            print(f"  - {table[0]}")
    else:
        print("尚未创建任何表")


if __name__ == '__main__':
    print("\n" + "█" * 60)
    print("█" + " " * 58 + "█")
    print("█  市场情绪计算系统 - 数据库迁移脚本  █")
    print("█" + " " * 58 + "█")
    print("█" * 60)
    
    # 检查已存在的表
    check_existing_tables()
    
    # 询问是否创建表
    print("\n是否要创建表结构？(y/n)")
    answer = input("> ").strip().lower()
    
    if answer == 'y' or answer == 'yes':
        success = create_tables()
        if success:
            print("\n✅ 迁移完成！")
            sys.exit(0)
        else:
            print("\n❌ 迁移失败！")
            sys.exit(1)
    else:
        print("\n已取消迁移")
        sys.exit(0)
