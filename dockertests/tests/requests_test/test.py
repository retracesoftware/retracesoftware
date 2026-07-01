import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import requests


class GetHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if not self.path.startswith("/get"):
            self.send_response(404)
            self.end_headers()
            return

        parsed = urlparse(self.path)
        args = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        url = f"http://127.0.0.1:{self.server.server_port}{self.path}"
        body = json.dumps({"args": args, "url": url}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fetch_patient_data():
    port = free_port()
    server = HTTPServer(("127.0.0.1", port), GetHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/get?patient_id=p123&status=active"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        data = response.json()
        print("Response Data:", data, flush=True)

        assert data["args"] == {"patient_id": "p123", "status": "active"}
        assert data["url"] == url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_requests_with_io():
    fetch_patient_data()


if __name__ == "__main__":
    print("=== requests_test ===", flush=True)
    test_requests_with_io()
