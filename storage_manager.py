import os
from database import get_db_connection


def get_folder_size_mb(path):
    """Calculate total size (in MB) of all files inside a node's folder."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total += os.path.getsize(fp)
    return total / (1024 * 1024)


def select_best_node():
    """Pick the active storage node with the most free space."""
    conn = get_db_connection()
    nodes = conn.execute(
        'SELECT * FROM storage_nodes WHERE is_active = 1'
    ).fetchall()
    conn.close()

    best_node = None
    max_free_space = -1

    for node in nodes:
        used_mb = get_folder_size_mb(node['node_path'])
        free_mb = node['capacity_mb'] - used_mb
        if free_mb > max_free_space:
            max_free_space = free_mb
            best_node = node

    return best_node
