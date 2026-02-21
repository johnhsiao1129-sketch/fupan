"""查看数据库中的日期"""
from datetime import datetime
import sqlite3

conn = sqlite3.connect('data/fupan.db')
cursor = conn.cursor()

print("=== first_limits 表中的日期 ===")
cursor.execute('SELECT DISTINCT limit_date FROM first_limits ORDER BY limit_date')
for row in cursor.fetchall():
    print(f"  {row[0]}")

print("\n=== topic_stock_relations 表中的日期 ===")
cursor.execute('SELECT DISTINCT date FROM topic_stock_relations WHERE relation_type = \"first_limit\" ORDER BY date')
for row in cursor.fetchall():
    print(f"  {row[0]}")

print("\n=== 当前首板数据（按日期分组统计）==")
cursor.execute('SELECT limit_date, COUNT(*) as cnt FROM first_limits GROUP BY limit_date ORDER BY limit_date')
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]} 条")

conn.close()
