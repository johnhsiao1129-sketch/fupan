import sqlite3

conn = sqlite3.connect('data/fupan.db')
c = conn.cursor()

c.execute('SELECT COUNT(*) FROM limit_down')
print('limit_down count:', c.fetchone()[0])

c.execute('SELECT trade_date, COUNT(*) FROM limit_down GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5')
print('Recent dates:', c.fetchall())

conn.close()
