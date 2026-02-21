import sqlite3

conn = sqlite3.connect('data/fupan.db')
c = conn.cursor()

# 获取first_limits表的创建SQL
c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='first_limits'")
sql = c.fetchone()[0]

print("first_limits 表的创建SQL:")
print(sql)
print()

# 获取first_limit_topics表的创建SQL
c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='first_limit_topics'")
sql = c.fetchone()[0]

print("first_limit_topics 表的创建SQL:")
print(sql)
print()

# 获取索引信息
c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='first_limits'")
indexes = c.fetchall()
print("first_limits 表的索引:")
for idx in indexes:
    print(idx[0])
print()

conn.close()
