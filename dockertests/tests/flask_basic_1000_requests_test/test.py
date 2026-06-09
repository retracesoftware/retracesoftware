import os
import sys

from flask import Flask, jsonify


PORT = int(os.environ.get("FLASK_BASIC_1000_PORT", "5000"))

app = Flask(__name__)
state = {"hits": 0}


@app.route("/")
def index():
    state["hits"] += 1
    return jsonify(message="hello from flask_basic_1000_requests_test", hits=state["hits"])


def main() -> None:
    print("=== flask_basic_1000_requests_test ===", flush=True)
    print(f"Open: http://127.0.0.1:{PORT}/", flush=True)
    print("Drive requests with client.py, then interrupt this server.", flush=True)
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - top-level scenario diagnostics.
        print(f"test failed: {exc}", file=sys.stderr, flush=True)
        raise
