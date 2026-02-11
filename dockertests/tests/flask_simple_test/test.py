"""
Simple Flask server test - minimal server-side tracing coverage.

This test runs a real Flask server, sends HTTP requests to it, validates
stateful behavior, and shuts down cleanly. It is intentionally lightweight
but still exercises network + app logic paths relevant to retrace.
"""
import json
import socket
import threading
import time
import urllib.error
import urllib.request

from flask import Flask, jsonify
from werkzeug.serving import make_server

app = Flask(__name__)
request_count = 0


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/test")
def test_endpoint():
    global request_count
    request_count += 1
    return jsonify({"message": "Hello!", "count": request_count})


class ServerThread(threading.Thread):
    def __init__(self, host: str, port: int):
        super().__init__(daemon=True)
        self.server = make_server(host, port, app)
        self.context = app.app_context()
        self.context.push()

    def run(self):
        self.server.serve_forever()

    def shutdown(self):
        self.server.shutdown()
        self.context.pop()


def http_get_json(url: str, retries: int = 30, delay: float = 0.1) -> dict:
    last_exc = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            last_exc = exc
            time.sleep(delay)
    raise RuntimeError(f"Failed to GET {url}: {last_exc}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main():
    host = "127.0.0.1"
    port = free_port()
    base_url = f"http://{host}:{port}"

    print("Starting Flask server...", flush=True)
    server = ServerThread(host, port)
    server.start()

    try:
        health_resp = http_get_json(f"{base_url}/health")
        assert health_resp.get("status") == "ok"

        first = http_get_json(f"{base_url}/test")
        second = http_get_json(f"{base_url}/test")

        assert first["message"] == "Hello!"
        assert first["count"] == 1
        assert second["count"] == 2

        print("Flask simple test passed", flush=True)
    finally:
        server.shutdown()
        server.join(timeout=5)
        assert not server.is_alive(), "Server thread failed to stop cleanly"


if __name__ == "__main__":
    main()
