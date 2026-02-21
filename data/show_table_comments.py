"""
快速查询表说明
用途：快速查看数据库表的用途和字段说明
"""

import sqlite3
import os
import sys

# 添加src目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = "../data/fupan.db"


def show_table_comments(table_name=None):
    """显示表说明"""
    if not os.path.exists(DB_PATH):
        print(f"❌ 数据库文件不存在：{DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        if table_name:
            cursor.execute('''
                SELECT table_name, description, key_fields, created_at
                FROM table_comments
                WHERE table_name = ?
            ''', (table_name,))
            rows = cursor.fetchall()
        else:
            cursor.execute('SELECT table_name, description, key_fields, created_at FROM table_comments ORDER BY table_name')
            rows = cursor.fetchall()

        if not rows:
            print(f"{'未找到说明' if table_name else '表说明表为空'}")
        else:
            for row in rows:
                print(f"\n{'='*80}")
                print(f"表名: {row[0]}")
                print(f"{'='*80}")
                print(f"\n说明:\n{row[1]}")
                if row[2]:
                    print(f"\n关键字段: {row[2]}")
                print(f"\n创建时间: {row[3]}")

        conn.close()
    except Exception as e:
        print(f"❌ 查询失败：{e}")

if __name__ == '__main__':
    import sys
    
    # 如果提供了表名作为参数，只显示该表的说明
    if len(sys.argv) > 1:
        show_table_comments(sys.argv[1])
    else:
        print("查看所有表说明:")
        print("用法: python show_table_comments.py [table_name]")
        print("="*80)
        show_table_comments()
