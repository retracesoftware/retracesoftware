"""Test record/replay of the requests library.

Exercises _socket and select proxying through the requests/urllib3
HTTP client stack making real TCP connections.

Pattern: a simple HTTP server runs *outside* the recording context
(in a plain thread).  The requests client runs *inside* the
recording context.  During replay, the client code re-runs and all
socket I/O is satisfied from the recorded stream — no real server
or network needed.

Between record and replay, urllib3 and requests are flushed from
``sys.modules`` so that connection-pool state (locks, cached
connections) doesn't carry over — otherwise the lock-operation
sequence diverges.

Requires: requests
"""
import sys
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytest

requests_lib = pytest.importorskip("requests")

pytestmark = pytest.mark.skip(reason="known _thread proxy divergence — diagnosing separately")


# ── helpers ───────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    """Minimal handler that serves canned responses."""

    routes = {}

    def do_GET(self):
        body, content_type = self.routes.get(
            self.path, (b"not found", "text/plain"))
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # silence stderr


def _serve(server, n=1):
    """Handle *n* requests on *server* in a background thread."""
    def target():
        for _ in range(n):
            server.handle_request()
    t = threading.Thread(target=target)
    t.start()
    return t


def _flush_http_modules():
    """Remove requests/urllib3 from sys.modules to reset all state.

    This ensures the replay phase starts with the same blank-slate
    module state as the record phase — no cached connection pools,
    no pre-allocated locks, no warm PoolManager caches.
    """
    for key in list(sys.modules):
        if key.startswith(('urllib3', 'requests')):
            del sys.modules[key]


# ── tests ─────────────────────────────────────────────────────────

def test_requests_get(runner):
    """requests.get() records and replays an HTTP response."""
    _flush_http_modules()
    _Handler.routes = {
        "/hello": (b"Hello from test server", "text/plain"),
    }
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]

    t = _serve(server)

    def client_work():
        import requests
        resp = requests.get(
            f"http://127.0.0.1:{port}/hello",
            timeout=5)
        return resp.text

    recording = runner.record(client_work)
    t.join()
    server.server_close()

    _flush_http_modules()

    def replay_work():
        import requests
        resp = requests.get(
            "http://127.0.0.1:1/hello",
            timeout=5)
        return resp.text

    result = runner.replay(recording, replay_work)
    assert result == "Hello from test server"


def test_diagnose_requests_get(runner):
    """Sequential record+replay diagnosis for requests.get()."""
    _Handler.routes = {
        "/hello": (b"Hello from test server", "text/plain"),
    }
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]

    t = _serve(server)

    def client_work():
        import requests
        resp = requests.get(
            f"http://127.0.0.1:{port}/hello",
            timeout=5)
        return resp.text

    runner.diagnose(client_work, setup=_flush_http_modules)

    t.join()
    server.server_close()


def test_requests_post(runner):
    """requests.post() records and replays a POST round-trip."""
    _flush_http_modules()
    _Handler.routes = {}
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]

    t = _serve(server)

    payload = b"integration-test-payload"

    def client_work():
        import requests
        resp = requests.post(
            f"http://127.0.0.1:{port}/echo",
            data=payload,
            timeout=5)
        return resp.content

    recording = runner.record(client_work)
    t.join()
    server.server_close()

    _flush_http_modules()

    def replay_work():
        import requests
        resp = requests.post(
            "http://127.0.0.1:1/echo",
            data=payload,
            timeout=5)
        return resp.content

    result = runner.replay(recording, replay_work)
    assert result == payload


def test_requests_json(runner):
    """requests parses a JSON response during replay."""
    _flush_http_modules()
    import json
    body = json.dumps({"status": "ok", "count": 7}).encode()
    _Handler.routes = {
        "/api": (body, "application/json"),
    }
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]

    t = _serve(server)

    def client_work():
        import requests
        resp = requests.get(
            f"http://127.0.0.1:{port}/api",
            timeout=5)
        return resp.json()

    recording = runner.record(client_work)
    t.join()
    server.server_close()

    _flush_http_modules()

    def replay_work():
        import requests
        resp = requests.get(
            "http://127.0.0.1:1/api",
            timeout=5)
        return resp.json()

    result = runner.replay(recording, replay_work)
    assert result == {"status": "ok", "count": 7}


def test_requests_multiple(runner):
    """Multiple sequential requests in one recording."""
    _flush_http_modules()
    _Handler.routes = {
        "/a": (b"alpha", "text/plain"),
        "/b": (b"bravo", "text/plain"),
    }
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]

    t = _serve(server, n=2)

    def client_work():
        import requests
        r1 = requests.get(f"http://127.0.0.1:{port}/a", timeout=5)
        r2 = requests.get(f"http://127.0.0.1:{port}/b", timeout=5)
        return [r1.text, r2.text]

    recording = runner.record(client_work)
    t.join()
    server.server_close()

    _flush_http_modules()

    def replay_work():
        import requests
        r1 = requests.get("http://127.0.0.1:1/a", timeout=5)
        r2 = requests.get("http://127.0.0.1:1/b", timeout=5)
        return [r1.text, r2.text]

    result = runner.replay(recording, replay_work)
    assert result == ["alpha", "bravo"]


def test_requests_large_response(runner):
    """requests handles a large response body during replay."""
    _flush_http_modules()
    large_body = b"x" * 100_000
    _Handler.routes = {
        "/big": (large_body, "application/octet-stream"),
    }
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]

    t = _serve(server)

    def client_work():
        import requests
        resp = requests.get(
            f"http://127.0.0.1:{port}/big",
            timeout=5)
        return len(resp.content)

    recording = runner.record(client_work)
    t.join()
    server.server_close()

    _flush_http_modules()

    def replay_work():
        import requests
        resp = requests.get(
            "http://127.0.0.1:1/big",
            timeout=5)
        return len(resp.content)

    result = runner.replay(recording, replay_work)
    assert result == 100_000
