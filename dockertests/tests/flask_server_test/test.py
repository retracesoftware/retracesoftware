"""
Flask server with database interactions - THIS IS THE TEST.

This is the Flask application being tested under retrace.
Retrace records all database operations during record phase,
and replays them during replay phase.
"""
from flask import Flask, jsonify, request
import sqlite3
import time
import os

app = Flask(__name__)

# Database file
DB_FILE = os.environ.get('DB_FILE', '/tmp/app.db')

def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database schema."""
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()
    print("Database initialized")

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({'status': 'healthy', 'timestamp': time.time()})

@app.route('/api/users', methods=['GET'])
def get_users():
    """Get all users from database."""
    conn = get_db()
    users = conn.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()
    conn.close()
    
    return jsonify({
        'users': [dict(row) for row in users],
        'count': len(users)
    })

@app.route('/api/users', methods=['POST'])
def create_user():
    """Create a new user in database."""
    data = request.get_json()
    name = data.get('name')
    email = data.get('email')
    
    if not name or not email:
        return jsonify({'error': 'Name and email required'}), 400
    
    conn = get_db()
    cursor = conn.execute(
        'INSERT INTO users (name, email, created_at) VALUES (?, ?, ?)',
        (name, email, time.time())
    )
    user_id = cursor.lastrowid
    conn.commit()
    
    # Fetch the created user
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    
    return jsonify(dict(user)), 201

@app.route('/api/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    """Get a specific user."""
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify(dict(user))

@app.route('/api/users/<int:user_id>/posts', methods=['GET'])
def get_user_posts(user_id):
    """Get all posts for a user."""
    conn = get_db()
    posts = conn.execute(
        'SELECT * FROM posts WHERE user_id = ? ORDER BY created_at DESC',
        (user_id,)
    ).fetchall()
    conn.close()
    
    return jsonify({
        'posts': [dict(row) for row in posts],
        'count': len(posts)
    })

@app.route('/api/posts', methods=['POST'])
def create_post():
    """Create a new post."""
    data = request.get_json()
    user_id = data.get('user_id')
    title = data.get('title')
    content = data.get('content')
    
    if not all([user_id, title, content]):
        return jsonify({'error': 'user_id, title, and content required'}), 400
    
    conn = get_db()
    
    # Verify user exists
    user = conn.execute('SELECT id FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'User not found'}), 404
    
    cursor = conn.execute(
        'INSERT INTO posts (user_id, title, content, created_at) VALUES (?, ?, ?, ?)',
        (user_id, title, content, time.time())
    )
    post_id = cursor.lastrowid
    conn.commit()
    
    # Fetch the created post
    post = conn.execute('SELECT * FROM posts WHERE id = ?', (post_id,)).fetchone()
    conn.close()
    
    return jsonify(dict(post)), 201

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get database statistics."""
    conn = get_db()
    user_count = conn.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
    post_count = conn.execute('SELECT COUNT(*) as count FROM posts').fetchone()['count']
    conn.close()
    
    return jsonify({
        'users': user_count,
        'posts': post_count,
        'timestamp': time.time()
    })

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
