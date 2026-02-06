"""
Load generator client - sends requests to the Flask server.

This is infrastructure, not the test. It generates load for the server to handle.
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
            response = requests.get(f"{BASE_URL}/health", timeout=1)
            if response.status_code == 200:
                print("✓ Flask server is ready!")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.5)
    print("✗ Flask server not ready")
    return False


def generate_load():
    """Generate load against the Flask server."""
    print("\n" + "=" * 60)
    print("Generating load for Flask server")
    print("=" * 60)

    # Test home endpoint
    print("\n📍 GET /")
    r = requests.get(f"{BASE_URL}/")
    print(f"   {r.status_code}: {r.json()}")

    # Create some users
    print("\n📍 POST /api/users")
    for name in ["Alice", "Bob", "Charlie"]:
        r = requests.post(f"{BASE_URL}/api/users", json={
            "name": name,
            "email": f"{name.lower()}@example.com"
        })
        print(f"   Created: {r.json()}")

    # Fetch users
    print("\n📍 GET /api/users/<id>")
    for i in range(1, 4):
        r = requests.get(f"{BASE_URL}/api/users/{i}")
        print(f"   User {i}: {r.json()}")

    # Counter test
    print("\n📍 GET /api/count (5 times)")
    for _ in range(5):
        r = requests.get(f"{BASE_URL}/api/count")
        print(f"   Count: {r.json()['count']}")

    print("\n" + "=" * 60)
    print("✅ Load generation complete")
    print("=" * 60)


if __name__ == '__main__':
    if not wait_for_server():
        sys.exit(1)
    generate_load()
