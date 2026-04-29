"""
Retraced client for the Flask HTTP replay scenario.
"""
import os
import sys
import time

import requests


BASE_URL = os.environ.get("FLASK_URL") or os.environ.get("SERVER_URL", "http://localhost:5000")


def wait_for_server(session, max_retries=30):
    print(f"Waiting for Flask server at {BASE_URL}...")
    for _ in range(max_retries):
        try:
            response = session.get(f"{BASE_URL}/health", timeout=1)
            if response.status_code == 200:
                print("Flask server is ready!")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.5)
    print("Flask server not ready")
    return False


def generate_load(session):
    print("\n" + "=" * 60)
    print("Generating load for Flask server")
    print("=" * 60)

    print("\nGET /")
    response = session.get(f"{BASE_URL}/")
    print(f"   {response.status_code}: {response.json()}")

    print("\nPOST /api/users")
    for name in ["Alice", "Bob", "Charlie"]:
        response = session.post(f"{BASE_URL}/api/users", json={
            "name": name,
            "email": f"{name.lower()}@example.com",
        })
        print(f"   Created: {response.json()}")

    print("\nGET /api/users/<id>")
    for user_id in range(1, 4):
        response = session.get(f"{BASE_URL}/api/users/{user_id}")
        print(f"   User {user_id}: {response.json()}")

    print("\nGET /api/count (5 times)")
    for _ in range(5):
        response = session.get(f"{BASE_URL}/api/count")
        print(f"   Count: {response.json()['count']}")

    print("\n" + "=" * 60)
    print("Load generation complete")
    print("=" * 60)


if __name__ == "__main__":
    with requests.Session() as http:
        if not wait_for_server(http):
            sys.exit(1)
        generate_load(http)
