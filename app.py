import os
import hashlib
import sqlite3
import uuid
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from database import get_db_connection, init_db, seed_nodes
from storage_manager import select_best_node, get_folder_size_mb

app = Flask(__name__)
app.secret_key = 'change-this-to-something-random-later'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB upload limit

init_db()
seed_nodes()


# ---------- Helpers ----------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Admin access required.')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


@app.errorhandler(413)
def file_too_large(e):
    flash('File too large. Max size is 50MB.')
    return redirect(url_for('dashboard'))


# ---------- Public routes ----------

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        try:
            conn.execute(
                'INSERT INTO users (username, email, password) VALUES (?, ?, ?)',
                (username, email, hashed_password)
            )
            conn.commit()
            flash('Registration successful! Please login.')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username or email already exists.')
            return redirect(url_for('register'))
        finally:
            conn.close()

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        user = conn.execute(
            'SELECT * FROM users WHERE username = ?', (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.')
            return redirect(url_for('login'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


# ---------- Authenticated routes ----------

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    files = conn.execute(
        '''SELECT files.*, storage_nodes.node_name FROM files
           LEFT JOIN storage_nodes ON files.node_id = storage_nodes.id
           WHERE files.owner_id = ? AND files.is_trashed = 0''',
        (session['user_id'],)
    ).fetchall()
    folders = conn.execute(
        'SELECT * FROM folders WHERE owner_id = ?',
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template(
        'dashboard.html',
        username=session['username'],
        role=session.get('role'),
        files=files,
        folders=folders
    )


@app.route('/create_folder', methods=['POST'])
@login_required
def create_folder():
    folder_name = request.form['folder_name']
    parent_id = request.form.get('parent_id') or None

    conn = get_db_connection()
    conn.execute(
        'INSERT INTO folders (name, owner_id, parent_id) VALUES (?, ?, ?)',
        (folder_name, session['user_id'], parent_id)
    )
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))


@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files or request.files['file'].filename == '':
        flash('No file selected.')
        return redirect(url_for('dashboard'))

    file = request.files['file']
    folder_id = request.form.get('folder_id') or None

    original_name = secure_filename(file.filename)
    if original_name == '':
        flash('Invalid filename.')
        return redirect(url_for('dashboard'))

    node = select_best_node()
    if node is None:
        flash('No storage node available.')
        return redirect(url_for('dashboard'))

    unique_name = f"{uuid.uuid4().hex}_{original_name}"
    filepath = os.path.join(node['node_path'], unique_name)

    file.save(filepath)

    filesize = os.path.getsize(filepath)
    with open(filepath, 'rb') as f:
        filehash = hashlib.sha256(f.read()).hexdigest()

    conn = get_db_connection()
    conn.execute(
        '''INSERT INTO files (filename, stored_name, owner_id, folder_id, node_id, filesize, filehash)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (original_name, unique_name, session['user_id'], folder_id, node['id'], filesize, filehash)
    )
    conn.commit()
    conn.close()

    flash(f'File uploaded successfully to {node["node_name"]}.')
    return redirect(url_for('dashboard'))


@app.route('/download/<int:file_id>')
@login_required
def download_file(file_id):
    conn = get_db_connection()
    file = conn.execute(
        'SELECT * FROM files WHERE id = ?', (file_id,)
    ).fetchone()

    if file is None:
        conn.close()
        flash('File not found.')
        return redirect(url_for('dashboard'))

    is_owner = file['owner_id'] == session['user_id']

    share = conn.execute(
        'SELECT * FROM shares WHERE file_id = ? AND shared_with_id = ?',
        (file_id, session['user_id'])
    ).fetchone()

    has_download_permission = share and share['permission'] == 'download'

    if not is_owner and not has_download_permission:
        conn.close()
        flash('You do not have permission to download this file.')
        return redirect(url_for('dashboard'))

    node = conn.execute(
        'SELECT * FROM storage_nodes WHERE id = ?', (file['node_id'],)
    ).fetchone()
    conn.close()

    return send_from_directory(
        os.path.abspath(node['node_path']),
        file['stored_name'],
        as_attachment=True,
        download_name=file['filename']
    )


@app.route('/share', methods=['POST'])
@login_required
def share_file():
    file_id = request.form['file_id']
    target_username = request.form['username']
    permission = request.form.get('permission', 'view')

    conn = get_db_connection()

    file = conn.execute(
        'SELECT * FROM files WHERE id = ? AND owner_id = ?',
        (file_id, session['user_id'])
    ).fetchone()

    if file is None:
        conn.close()
        flash('File not found or you are not the owner.')
        return redirect(url_for('dashboard'))

    target_user = conn.execute(
        'SELECT * FROM users WHERE username = ?', (target_username,)
    ).fetchone()

    if target_user is None:
        conn.close()
        flash('User not found.')
        return redirect(url_for('dashboard'))

    if target_user['id'] == session['user_id']:
        conn.close()
        flash('You cannot share a file with yourself.')
        return redirect(url_for('dashboard'))

    existing = conn.execute(
        'SELECT * FROM shares WHERE file_id = ? AND shared_with_id = ?',
        (file_id, target_user['id'])
    ).fetchone()

    if existing:
        conn.execute(
            'UPDATE shares SET permission = ? WHERE id = ?',
            (permission, existing['id'])
        )
    else:
        conn.execute(
            'INSERT INTO shares (file_id, owner_id, shared_with_id, permission) VALUES (?, ?, ?, ?)',
            (file_id, session['user_id'], target_user['id'], permission)
        )

    conn.commit()
    conn.close()
    flash(f'File shared with {target_username}.')
    return redirect(url_for('dashboard'))


@app.route('/shared_with_me')
@login_required
def shared_with_me():
    conn = get_db_connection()
    shared_files = conn.execute(
        '''SELECT files.*, shares.permission, users.username as owner_name
           FROM shares
           JOIN files ON shares.file_id = files.id
           JOIN users ON shares.owner_id = users.id
           WHERE shares.shared_with_id = ? AND files.is_trashed = 0''',
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('shared_with_me.html', shared_files=shared_files)


@app.route('/delete/<int:file_id>', methods=['POST'])
@login_required
def delete_file(file_id):
    conn = get_db_connection()
    file = conn.execute(
        'SELECT * FROM files WHERE id = ? AND owner_id = ?',
        (file_id, session['user_id'])
    ).fetchone()

    if file is None:
        conn.close()
        flash('File not found or you are not the owner.')
        return redirect(url_for('dashboard'))

    conn.execute('UPDATE files SET is_trashed = 1 WHERE id = ?', (file_id,))
    conn.commit()
    conn.close()
    flash('File moved to trash.')
    return redirect(url_for('dashboard'))


@app.route('/trash')
@login_required
def trash():
    conn = get_db_connection()
    trashed_files = conn.execute(
        '''SELECT files.*, storage_nodes.node_name FROM files
           LEFT JOIN storage_nodes ON files.node_id = storage_nodes.id
           WHERE files.owner_id = ? AND files.is_trashed = 1''',
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('trash.html', trashed_files=trashed_files)


@app.route('/restore/<int:file_id>', methods=['POST'])
@login_required
def restore_file(file_id):
    conn = get_db_connection()
    file = conn.execute(
        'SELECT * FROM files WHERE id = ? AND owner_id = ?',
        (file_id, session['user_id'])
    ).fetchone()

    if file is None:
        conn.close()
        flash('File not found or you are not the owner.')
        return redirect(url_for('trash'))

    conn.execute('UPDATE files SET is_trashed = 0 WHERE id = ?', (file_id,))
    conn.commit()
    conn.close()
    flash('File restored.')
    return redirect(url_for('trash'))


@app.route('/delete_permanent/<int:file_id>', methods=['POST'])
@login_required
def delete_permanent(file_id):
    conn = get_db_connection()
    file = conn.execute(
        'SELECT * FROM files WHERE id = ? AND owner_id = ? AND is_trashed = 1',
        (file_id, session['user_id'])
    ).fetchone()

    if file is None:
        conn.close()
        flash('File not found, not trashed, or you are not the owner.')
        return redirect(url_for('trash'))

    node = conn.execute(
        'SELECT * FROM storage_nodes WHERE id = ?', (file['node_id'],)
    ).fetchone()

    filepath = os.path.join(node['node_path'], file['stored_name'])
    if os.path.exists(filepath):
        os.remove(filepath)

    conn.execute('DELETE FROM shares WHERE file_id = ?', (file_id,))
    conn.execute('DELETE FROM files WHERE id = ?', (file_id,))
    conn.commit()
    conn.close()
    flash('File permanently deleted.')
    return redirect(url_for('trash'))


# ---------- Admin routes ----------

@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db_connection()

    users = conn.execute('SELECT * FROM users').fetchall()
    nodes = conn.execute('SELECT * FROM storage_nodes').fetchall()

    total_files = conn.execute('SELECT COUNT(*) as c FROM files WHERE is_trashed = 0').fetchone()['c']
    total_users = conn.execute('SELECT COUNT(*) as c FROM users').fetchone()['c']
    total_storage_used = conn.execute(
        'SELECT SUM(filesize) as total FROM files WHERE is_trashed = 0'
    ).fetchone()['total'] or 0

    node_stats = []
    for node in nodes:
        used_mb = get_folder_size_mb(node['node_path'])
        node_stats.append({
            'id': node['id'],
            'name': node['node_name'],
            'path': node['node_path'],
            'capacity_mb': node['capacity_mb'],
            'used_mb': round(used_mb, 2),
            'free_mb': round(node['capacity_mb'] - used_mb, 2),
            'is_active': node['is_active'],
            'is_user_node': node['is_user_node']
        })

    conn.close()

    return render_template(
        'admin.html',
        users=users,
        node_stats=node_stats,
        total_files=total_files,
        total_users=total_users,
        total_storage_used=round(total_storage_used / (1024 * 1024), 2)
    )


@app.route('/admin/toggle_node/<int:node_id>', methods=['POST'])
@admin_required
def toggle_node(node_id):
    conn = get_db_connection()
    node = conn.execute('SELECT * FROM storage_nodes WHERE id = ?', (node_id,)).fetchone()
    if node:
        new_status = 0 if node['is_active'] else 1
        conn.execute('UPDATE storage_nodes SET is_active = ? WHERE id = ?', (new_status, node_id))
        conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))


if __name__ == '__main__':
    app.run(debug=True)