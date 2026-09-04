import sqlite3

DB_NAME = "sharespace.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            parent_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users (id),
            FOREIGN KEY (parent_id) REFERENCES folders (id)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS storage_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_name TEXT NOT NULL,
            node_path TEXT NOT NULL,
            capacity_mb INTEGER NOT NULL,
            is_active INTEGER DEFAULT 1,
            owner_user_id INTEGER,
            is_user_node INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_user_id) REFERENCES users (id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            folder_id INTEGER,
            node_id INTEGER,
            filesize INTEGER,
            filehash TEXT,
            is_trashed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users (id),
            FOREIGN KEY (folder_id) REFERENCES folders (id),
            FOREIGN KEY (node_id) REFERENCES storage_nodes (id)
        )
    ''')
    

    conn.commit()
    conn.close()

def seed_nodes():
    conn = get_db_connection()
    existing = conn.execute('SELECT COUNT(*) as c FROM storage_nodes').fetchone()['c']
    if existing == 0:
        nodes = [
            ('Node 1', 'storage_nodes/node1', 500),
            ('Node 2', 'storage_nodes/node2', 500),
            ('Node 3', 'storage_nodes/node3', 500),
        ]
        for name, path, cap in nodes:
            conn.execute(
                'INSERT INTO storage_nodes (node_name, node_path, capacity_mb) VALUES (?, ?, ?)',
                (name, path, cap)
            )
        conn.commit()
    conn.close()