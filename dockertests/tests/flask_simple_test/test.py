from flask import Flask, jsonify


def test_flask_request_context():
    app = Flask(__name__)

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    with app.test_request_context("/health"):
        response = health()

    assert response.get_json() == {"status": "ok"}
    print("Flask request context smoke passed", flush=True)


if __name__ == "__main__":
    print("=== flask_simple_test ===", flush=True)
    test_flask_request_context()
