import sqlite3

con = sqlite3.connect('file:journal.db?mode=ro', uri=True)
cur = con.cursor()
cur.execute('PRAGMA query_only = ON;')
tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
print('Tables in journal.db:', tables)
for t in tables:
    count = cur.execute(f"SELECT count(*) FROM {t[0]};").fetchone()[0]
    print(f"Table {t[0]}: {count} rows")
con.close()
