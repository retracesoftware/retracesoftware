"""
Simplest possible Flask test.

Run it:           python test.py
Open in browser:  http://127.0.0.1:5000/
Refresh a few times to bump the counter.
Stop the server: Ctrl-C

That's it.
"""

import os

from flask import Flask, jsonify

app = Flask(__name__)

state = {"hits": 0}


@app.route("/")
def index():
    state["hits"] += 1
    return jsonify(
        message="hello from flask_basic_test",
        hits=state["hits"],
    )


@app.route("/health")
def health():
    return jsonify(status="ok")


if __name__ == "__main__":
    host = os.environ.get("FLASK_BASIC_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_BASIC_PORT", "5000"))
    print("=" * 60)
    print("Flask basic test running.")
    print(f"Open: http://127.0.0.1:{port}/")
    print("Refresh the page a few times, then press Ctrl-C here.")
    print("=" * 60)
    app.run(host=host, port=port, debug=False, use_reloader=False)
