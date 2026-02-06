"""
Simple load generator client - curls the Flask server.

This runs in a separate container to generate HTTP traffic.
"""
import requests
import time
import os
import sys

BASE_URL = os.environ.get('FLASK_URL') or os.environ.get('SERVER_URL', 'http://localhost:5000')

def wait_for_server(max_retries=30):
    """Wait for Flask server to be ready."""
    print(f"Waiting for Flask server at {BASE_URL}...")
    for i in range(max_retries):
        try:
            response = requests.get(f"{BASE_URL}/health", timeout=2)
            if response.status_code == 200:
                print("✓ Flask server is ready!")
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.5)
        if (i + 1) % 10 == 0:
            print(f"  Still waiting... ({i+1}/{max_retries})")
    
    raise RuntimeError("Flask server did not become ready in time")

def run_load():
    """Generate load against the Flask server."""
    print("=" * 60)
    print("Flask Server Test - Load Generator")
    print("=" * 60)
    
    wait_for_server()
    
    # Create some users
    print("\n📝 Creating users...")
    users = []
    for i in range(3):
        response = requests.post(f"{BASE_URL}/api/users", json={
            'name': f'User {i+1}',
            'email': f'user{i+1}@example.com'
        })
        assert response.status_code == 201, f"Failed to create user: {response.status_code}"
        user = response.json()
        users.append(user)
        print(f"  ✓ Created user {user['id']}: {user['name']}")
    
    # Get all users
    print("\n📋 Fetching all users...")
    response = requests.get(f"{BASE_URL}/api/users")
    assert response.status_code == 200
    data = response.json()
    print(f"  ✓ Found {data['count']} users")
    
    # Create posts for each user
    print("\n📝 Creating posts...")
    for user in users:
        for j in range(2):
            response = requests.post(f"{BASE_URL}/api/posts", json={
                'user_id': user['id'],
                'title': f'Post {j+1} by {user["name"]}',
                'content': f'This is post content {j+1}'
            })
            assert response.status_code == 201
            post = response.json()
            print(f"  ✓ Created post {post['id']}: {post['title']}")
    
    # Get posts for each user
    print("\n📖 Fetching user posts...")
    for user in users:
        response = requests.get(f"{BASE_URL}/api/users/{user['id']}/posts")
        assert response.status_code == 200
        data = response.json()
        print(f"  ✓ User {user['name']} has {data['count']} posts")
    
    # Get statistics
    print("\n📊 Getting stats...")
    response = requests.get(f"{BASE_URL}/api/stats")
    assert response.status_code == 200
    stats = response.json()
    print(f"  ✓ Total users: {stats['users']}")
    print(f"  ✓ Total posts: {stats['posts']}")
    
    # Verify counts
    assert stats['users'] == 3, f"Expected 3 users, got {stats['users']}"
    assert stats['posts'] == 6, f"Expected 6 posts, got {stats['posts']}"
    
    print("\n" + "=" * 60)
    print("✅ All operations completed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    try:
        run_load()
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
