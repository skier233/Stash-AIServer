def upgrade(conn):
    conn.exec_driver_sql("""CREATE TABLE IF NOT EXISTS example_plugin_demo (
        id INTEGER PRIMARY KEY,
        note TEXT
    )""")
