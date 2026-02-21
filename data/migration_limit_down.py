"""
跌停历史表迁移脚本
说明：
- 将 daily_lows 表替换为 limit_down_history 表
- limit_down_history 记录每只连续跌停股票的详细信息（类似 continuous_limits_history）
"""

import sqlite3
import os

DB_PATH = "../data/fupan.db"


def migrate_table():
    """迁移表结构"""
    print("=" * 60)
    print("开始跌停历史表迁移...")
    print("=" * 60)

    if not os.path.exists(DB_PATH):
        print(f"❌ 数据库文件不存在：{DB_PATH}")
        return False

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 检查 daily_lows 表是否存在
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='daily_lows'
        """)
        exists = cursor.fetchone()

        if exists:
            print("\n[1/2] 删除旧表 daily_lows...")
            cursor.execute("DROP TABLE IF EXISTS daily_lows")
            print("✓ 删除成功")
        else:
            print("\n[1/2] daily_lows 表不存在，跳过删除")

        # 创建新表 limit_down_history
        print("\n[2/2] 创建新表 limit_down_history...")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS limit_down_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                price REAL,
                change_percent REAL,
                continuous_days INTEGER NOT NULL,
                sector TEXT,
                reason TEXT,
                amount REAL,
                create_time TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        print("✓ 创建成功")

        # 创建索引
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_limit_down_history_trade_date
            ON limit_down_history(trade_date)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_limit_down_history_code
            ON limit_down_history(code)
        ''')
        print("✓ 创建索引")

        conn.commit()

        print("\n" + "=" * 60)
        print("✅ 跌停历史表迁移完成！")
        print("=" * 60)

        return True

    except Exception as e:
        conn.rollback()
        print(f"\n❌ 迁移失败：{e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        conn.close()


if __name__ == '__main__':
    print("\n跌停历史表迁移脚本")
    print("是否要执行迁移？(y/n)")
    answer = input("> ").strip().lower()

    if answer in ['y', 'yes']:
        if migrate_table():
            print("\n✅ 迁移成功！")
        else:
            print("\n❌ 迁移失败！")
    else:
        print("\n已取消迁移")
