"""强制修复所有可能的错误日期"""
import sqlite3

DB_PATH = "data/fupan.db"

print("=== 开始强制修复所有可能的错误日期 ===")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

print("\n1. 修复 first_limits 表")
print("   检查 2026 年 2 月的所有日期...")

# 检查所有 2026-02- 系列的日期
cursor.execute("SELECT COUNT(*) FROM first_limits WHERE limit_date LIKE '2026-02%'")
total_feb = cursor.fetchone()[0]
print(f"   2026-02 月共有 {total_feb} 条记录")

# 详细列出所有 2 月的记录
cursor.execute('''
    SELECT id, stock_id, limit_date, first_limit_time, limit_type, reason
    FROM first_limits
    WHERE limit_date LIKE '2026-02%'
    ORDER BY limit_date, id
''')
feb_records = cursor.fetchall()
if feb_records:
    print("\n   2 月的记录详情:")
    for record in feb_records:
        print(f"      ID={record[0]}, stock_id={record[1]}, date={record[2]}, time={record[3]}, type={record[4]}, reason={record[5]}")
else:
    print("   没有找到 2 月的记录")

# 修复所有非交易日的 2 月数据
# 2026-02-01 (周日) 和 2026-02-02 (周一) 都是非交易日
non_trading_days = ['2026-02-01', '2026-02-02', '2026-02-07', '2026-02-08']
target_date = '2026-01-30'  # 最近的一个交易日

for non_trading in non_trading_days:
    cursor.execute("SELECT COUNT(*) FROM first_limits WHERE limit_date = ?", (non_trading,))
    count = cursor.fetchone()[0]
    if count > 0:
        print(f"\n   修复 {non_trading} 的 {count} 条记录到 {target_date}")
        cursor.execute("UPDATE first_limits SET limit_date = ? WHERE limit_date = ?", (target_date, non_trading))
        print(f"   已修改 {cursor.rowcount} 条记录")

# 确认修改
conn.commit()

print("\n2. 验证修复结果")
cursor.execute("SELECT COUNT(*) FROM first_limits WHERE limit_date LIKE '2026-02%'")
total_feb_after = cursor.fetchone()[0]
print(f"   2026-02 月共有 {total_feb_after} 条记录（修复后）")

# 显示当前所有日期
cursor.execute('''
    SELECT limit_date, COUNT(*) as cnt
    FROM first_limits
    GROUP BY limit_date
    ORDER BY limit_date DESC
''')
print("\n   当前 first_limits 表的日期分布:")
for row in cursor.fetchall():
    print(f"      {row[0]}: {row[1]} 条")

print("\n3. 修复 topic_stock_relations 表")
cursor.execute("SELECT COUNT(*) FROM topic_stock_relations WHERE date LIKE '2026-02%'")
total_feb_tsr = cursor.fetchone()[0]
print(f"   2026-02 月共有 {total_feb_tsr} 条记录")

# 详细列出所有 2 月的记录
cursor.execute('''
    SELECT id, topic_id, stock_id, date, relation_type
    FROM topic_stock_relations
    WHERE date LIKE '2026-02%'
    ORDER BY date, id
''')
feb_records_tsr = cursor.fetchall()
if feb_records_tsr:
    print("\n   2 月的记录详情:")
    for record in feb_records_tsr:
        print(f"      ID={record[0]}, topic_id={record[1]}, stock_id={record[2]}, date={record[3]}, type={record[4]}")
else:
    print("   没有找到 2 月的记录")

# 修复所有非交易日的 2 月数据
for non_trading in non_trading_days:
    cursor.execute("SELECT COUNT(*) FROM topic_stock_relations WHERE date = ?", (non_trading,))
    count = cursor.fetchone()[0]
    if count > 0:
        print(f"\n   修复 {non_trading} 的 {count} 条记录到 {target_date}")
        cursor.execute("UPDATE topic_stock_relations SET date = ? WHERE date = ?", (target_date, non_trading))
        print(f"   已修改 {cursor.rowcount} 条记录")

# 确认修改
conn.commit()

# 显示当前所有日期
cursor.execute('''
    SELECT date, relation_type, COUNT(*) as cnt
    FROM topic_stock_relations
    GROUP BY date, relation_type
    ORDER BY date DESC
''')
print("\n   当前 topic_stock_relations 表的日期分布:")
for row in cursor.fetchall():
    print(f"      {row[0]}: {row[2]} 条 (type={row[1]})")

conn.close()

print("\n=== 所有修复完成 ===")
