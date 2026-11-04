import pymysql

creds = [
    {'user': 'klein', 'password': 'klein123'},
    {'user': 'root', 'password': ''},
    {'user': 'root', 'password': 'root'},
    {'user': 'root', 'password': 'password'},
]

for cred in creds:
    try:
        print(f"Trying: user={cred['user']}")
        conn = pymysql.connect(host='127.0.0.1', port=3306, **cred)
        print(f"Connected with user={cred['user']}")
        cur = conn.cursor()
        cur.execute('SHOW DATABASES')
        dbs = [r[0] for r in cur.fetchall()]
        print("Databases:", dbs)
        
        if 'klein_ai' in dbs:
            cur.execute('USE klein_ai')
            cur.execute('SHOW TABLES')
            tables = [r[0] for r in cur.fetchall()]
            print("Tables:", tables)
            
            if 'generation_upstream_log' in tables:
                cur.execute('SELECT COUNT(*) FROM generation_upstream_log')
                count = cur.fetchone()[0]
                print(f"Upstream log entries: {count}")
                
                if count > 0:
                    cur.execute('SELECT * FROM generation_upstream_log ORDER BY id DESC LIMIT 10')
                    cols = [d[0] for d in cur.description]
                    print("Columns:", cols)
                    for row in cur.fetchall():
                        print(dict(zip(cols, row)))
        
        conn.close()
        break
    except Exception as e:
        print(f"  Failed: {e}")
