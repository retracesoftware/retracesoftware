from datetime import datetime, timezone
import json
import random
import uuid

from flask import Flask, jsonify, request


app = Flask(__name__)
USERS = {}


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "request_id": str(uuid.uuid4()),
        }
    )


@app.post("/users")
def create_user():
    payload = request.get_json(force=True)
    user_id = len(USERS) + 1
    USERS[user_id] = {
        "id": user_id,
        "name": payload["name"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lucky_number": random.randint(1, 100),
    }
    return jsonify(USERS[user_id]), 201


@app.get("/users/<int:user_id>")
def get_user(user_id):
    user = USERS.get(user_id)
    if user is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(user)


@app.get("/summary")
def summary():
    return jsonify(
        {
            "user_count": len(USERS),
            "served_at": datetime.now(timezone.utc).isoformat(),
            "trace_hint": "Replay should return the recorded timestamps and random values.",
        }
    )


def print_response(label, response):
    body = response.get_json()
    print(
        f"{label}: status={response.status_code} body={json.dumps(body, sort_keys=True)}",
        flush=True,
    )


def main():
    print("=== Retrace Flask demo ===", flush=True)
    with app.test_client() as client:
        print_response("GET /health", client.get("/health"))
        print_response("POST /users Ada", client.post("/users", json={"name": "Ada"}))
        print_response("POST /users Grace", client.post("/users", json={"name": "Grace"}))
        print_response("GET /users/1", client.get("/users/1"))
        print_response("GET /summary", client.get("/summary"))
    print("Flask demo complete.", flush=True)


if __name__ == "__main__":
    main()
