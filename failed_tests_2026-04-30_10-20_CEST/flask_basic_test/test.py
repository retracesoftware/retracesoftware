"""
Simplest possible Flask test.

Run it:           python test.py
Open in browser:  http://127.0.0.1:5000/
Refresh a few times to bump the counter.
Stop the server: Ctrl-C

That's it.
"""

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


if __name__ == "__main__":
    print("=" * 60)
    print("Flask basic test running.")
    print("Open: http://127.0.0.1:5000/")
    print("Refresh the page a few times, then press Ctrl-C here.")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
