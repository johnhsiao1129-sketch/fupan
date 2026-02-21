import sqlite3

conn = sqlite3.connect('data/fupan.db')
c = conn.cursor()

c.execute('PRAGMA table_info(stocks)')
print('stocks table columns:')
for col in c.fetchall():
    print(f'  {col}')

print()
c.execute('SELECT * FROM stocks LIMIT 3')
print('Sample stocks records:')
for row in c.fetchall():
    print(f'  {row}')

conn.close()
