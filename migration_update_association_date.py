import sqlite3

conn = sqlite3.connect('data/fupan.db')
cursor = conn.cursor()

cursor.execute('''UPDATE first_limit_topics SET association_date = (SELECT limit_date FROM first_limits backup WHERE backup.id = first_limit_topics.first_limit_id)''')
conn.commit()
print(f'Updated {cursor.rowcount} rows')

cursor.execute('SELECT rowid, first_limit_id, topic_id, create_time, association_date FROM first_limit_topics LIMIT 10')
rows = cursor.fetchall()
for row in rows:
    print(row)

conn.close()
