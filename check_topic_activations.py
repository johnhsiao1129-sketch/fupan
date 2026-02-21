import sqlite3

conn = sqlite3.connect('data/fupan.db')
c = conn.cursor()

c.execute('PRAGMA table_info(topic_activations)')
print('topic_activations table columns:')
for col in c.fetchall():
    print(f'  {col}')

print()
c.execute('SELECT * FROM topic_activations LIMIT 5')
print('Sample topic_activations data:')
for row in c.fetchall():
    print(f'  {row}')

conn.close()
