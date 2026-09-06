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

from database import get_db_connection, init_db, seed_nodes
from storage_manager import select_best_node

app = Flask(__name__)
app.secret_key = 'change-this-to-something-random-later'

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

    node = select_best_node()
    if node is None:
        flash('No storage node available.')
        return redirect(url_for('dashboard'))

    original_name = file.filename
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


if __name__ == '__main__':
    app.run(debug=True)
