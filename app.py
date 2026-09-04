from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db_connection, init_db

app = Flask(__name__)
app.secret_key = 'change-this-to-something-random-later'

init_db()
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

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
            conn.close()
            flash('Registration successful! Please login.')
            return redirect(url_for('login'))
        except conn.IntegrityError:
            conn.close()
            flash('Username or email already exists.')
            return redirect(url_for('register'))

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

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    files = conn.execute(
        'SELECT * FROM files WHERE owner_id = ? AND is_trashed = 0',
        (session['user_id'],)
    ).fetchall()
    folders = conn.execute(
        'SELECT * FROM folders WHERE owner_id = ?',
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('dashboard.html', username=session['username'], files=files, folders=folders)
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)

import os
import hashlib
import uuid
from flask import send_from_directory

UPLOAD_FOLDER = 'storage'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

#file upload route
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

