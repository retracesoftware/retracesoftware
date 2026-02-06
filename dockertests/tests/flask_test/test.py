"""
Flask server test - the server runs under retrace.

This test demonstrates recording/replaying a Flask server's internal behavior.
The client is infrastructure that generates load - only the server is recorded.
"""
from flask import Flask, jsonify, request
import time
import os

app = Flask(__name__)

# In-memory data store (will be recorded/replayed)
users = {}
counter = 0


@app.route('/')
def home():
    return jsonify({
        'message': 'Hello from Flask!',
        'timestamp': time.time()
    })


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})


@app.route('/api/users/<int:user_id>')
def get_user(user_id):
    if user_id in users:
        return jsonify(users[user_id])
    return jsonify({
        'id': user_id,
        'name': f'User {user_id}',
        'email': f'user{user_id}@example.com'
    })


@app.route('/api/users', methods=['POST'])
def create_user():
    data = request.get_json()
    user_id = len(users) + 1
    users[user_id] = {
        'id': user_id,
        'name': data.get('name', 'Unknown'),
        'email': data.get('email', 'unknown@example.com'),
        'created_at': time.time()
    }
    return jsonify(users[user_id]), 201


@app.route('/api/count')
def count():
    global counter
    counter += 1
    return jsonify({'count': counter})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Flask server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
