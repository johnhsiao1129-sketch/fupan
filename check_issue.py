import sqlite3

conn = sqlite3.connect('data/fupan.db')
cursor = conn.cursor()

# 1. 检查2026-01-21新金路记录
cursor.execute('SELECT * FROM continuous_limits_history WHERE trade_date = ? AND code = ?', ('2026-01-21', '000510'))
print('2026-01-21新金路continuous_limits_history记录:', cursor.fetchall())

# 2. 检查新金路stock_id
cursor.execute('SELECT stock_id FROM stocks WHERE stock_code = ?', ('000510',))
stock_id = cursor.fetchone()
print(f'\n新金路stock_id: {stock_id[0] if stock_id else "未找到"}')

# 3. 检查topic_stock_relations中是否有682
cursor.execute('SELECT * FROM topic_stock_relations WHERE stock_id = ?', (682,))
print(f'\ntopic_stock_relations中stock_id=682的记录: {cursor.fetchall()}')

# 4. 检查化工题材id
cursor.execute('SELECT topic_id FROM topics WHERE topic_name = ?', ('化工',))
topic_id = cursor.fetchone()
print(f'\n化工题材topic_id: {topic_id[0] if topic_id else "未找到"}')

# 5. 检查化工题材的股票
if topic_id:
    cursor.execute('SELECT stock_id FROM topic_stock_relations WHERE topic_id = ?', (topic_id[0],))
    print(f'化工题材关联的stock_id数量: {len(cursor.fetchall())}')

# 6. 检查2026-01-21化工题材的内容
cursor.execute('SELECT t.topic_name, ra.content FROM rotation_actives ra JOIN topics t ON ra.topic_id = t.topic_id WHERE ra.date = ? AND t.topic_name = ?', ('2026-01-21', '化工'))
print(f'\n2026-01-21化工题材rotation_actives内容: {cursor.fetchall()}')

# 7. 检查2026-02-09首板数据
cursor.execute('SELECT count(*) FROM first_limits WHERE limit_date = ?', ('2026-02-09',))
print(f'\n2026-02-09 first_limits数量: {cursor.fetchone()[0]}')

# 8. 检查连板表2026-02-09数据
cursor.execute('SELECT count(*) FROM continuous_limits_history WHERE trade_date = ?', ('2026-02-09',))
print(f'2026-02-09 continuous_limits_history数量: {cursor.fetchone()[0]}')

conn.close()
