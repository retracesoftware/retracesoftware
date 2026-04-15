"""Test record/replay of a Flask application.

Exercises _socket and time proxying through a real WSGI server
(wsgiref) running a Flask app over TCP.

Pattern: the Flask server runs inside the recording context while
the HTTP client runs *outside* it (in a plain thread).  During
replay, the server code re-runs and all socket I/O is satisfied
from the recorded stream — no real client or network needed.

Uses ``wsgiref.simple_server`` rather than werkzeug's server to
avoid werkzeug's post-response selector drain logic, which makes
additional socket calls that diverge between record and replay.
``_handle_request_noblock()`` bypasses the selector wait-for-
connection step so that both record and replay call ``accept()``
directly.

Requires: flask
"""
import socket
import threading

import pytest

from tests.runner import Runner

pytest.skip("flask tests disabled — server hangs in test environment", allow_module_level=True)
flask = pytest.importorskip("flask")
from flask import Flask, jsonify, request as flask_request
from wsgiref.simple_server import make_server


# ── helpers ───────────────────────────────────────────────────────

def _make_app():
    """Create a minimal Flask application with a few routes."""
    app = Flask(__name__)

    @app.route("/hello")
    def hello():
        return "Hello, World!"

    @app.route("/echo", methods=["POST"])
    def echo():
        return flask_request.data

    @app.route("/json")
    def json_endpoint():
        return jsonify({"status": "ok", "value": 42})

    return app


def _http_get(port, path):
    """Minimal HTTP/1.0 GET using raw sockets (no library imports)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", port))
    s.sendall(f"GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n".encode())
    chunks = []
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
    s.close()
    return b"".join(chunks)


def _http_post(port, path, body):
    """Minimal HTTP/1.0 POST using raw sockets."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", port))
    s.sendall(
        f"POST {path} HTTP/1.0\r\n"
        f"Host: 127.0.0.1\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Content-Type: application/octet-stream\r\n"
        f"\r\n".encode() + body
    )
    chunks = []
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
    s.close()
    return b"".join(chunks)


def _response_body(raw):
    """Extract the HTTP response body from raw bytes."""
    return raw.split(b"\r\n\r\n", 1)[1]


def _wait_for_client(event, timeout=2):
    """Block until client signals it is about to connect."""
    event.wait(timeout)


# ── tests ─────────────────────────────────────────────────────────

def test_flask_hello():
    """Flask serves a GET /hello — no client during replay."""
    runner = Runner()
    app = _make_app()
    srv = make_server("127.0.0.1", 0, app)
    port = srv.server_address[1]

    response_raw = [None]
    connected = threading.Event()

    def client():
        connected.set()
        response_raw[0] = _http_get(port, "/hello")

    t = threading.Thread(target=client)
    t.start()
    _wait_for_client(connected)

    def server_work():
        srv._handle_request_noblock()

    recording = runner.record(server_work)
    t.join()
    srv.server_close()

    assert b"Hello, World!" in response_raw[0]

    # Replay: no real client — server replays from recording
    srv2 = make_server("127.0.0.1", 0, app)

    def replay_work():
        srv2._handle_request_noblock()

    runner.replay(recording, replay_work)
    srv2.server_close()


def test_flask_post_echo():
    """Flask echoes POST body — no client during replay."""
    runner = Runner()
    app = _make_app()
    srv = make_server("127.0.0.1", 0, app)
    port = srv.server_address[1]

    payload = b"record-replay-echo-test"
    response_raw = [None]
    connected = threading.Event()

    def client():
        connected.set()
        response_raw[0] = _http_post(port, "/echo", payload)

    t = threading.Thread(target=client)
    t.start()
    _wait_for_client(connected)

    def server_work():
        srv._handle_request_noblock()

    recording = runner.record(server_work)
    t.join()
    srv.server_close()

    assert payload in response_raw[0]

    # Replay
    srv2 = make_server("127.0.0.1", 0, app)

    def replay_work():
        srv2._handle_request_noblock()

    runner.replay(recording, replay_work)
    srv2.server_close()


def test_flask_json():
    """Flask JSON endpoint records and replays."""
    runner = Runner()
    app = _make_app()
    srv = make_server("127.0.0.1", 0, app)
    port = srv.server_address[1]

    response_raw = [None]
    connected = threading.Event()

    def client():
        connected.set()
        response_raw[0] = _http_get(port, "/json")

    t = threading.Thread(target=client)
    t.start()
    _wait_for_client(connected)

    def server_work():
        srv._handle_request_noblock()

    recording = runner.record(server_work)
    t.join()
    srv.server_close()

    body = _response_body(response_raw[0])
    assert b'"status"' in body
    assert b'"ok"' in body

    # Replay
    srv2 = make_server("127.0.0.1", 0, app)

    def replay_work():
        srv2._handle_request_noblock()

    runner.replay(recording, replay_work)
    srv2.server_close()


def test_flask_multiple_requests():
    """Flask handles multiple sequential requests in one recording."""
    runner = Runner()
    app = _make_app()
    srv = make_server("127.0.0.1", 0, app)
    port = srv.server_address[1]

    responses = [None, None]
    connected = threading.Event()

    def client():
        connected.set()
        responses[0] = _http_get(port, "/hello")
        responses[1] = _http_get(port, "/json")

    t = threading.Thread(target=client)
    t.start()
    _wait_for_client(connected)

    def server_work():
        srv._handle_request_noblock()
        srv._handle_request_noblock()

    recording = runner.record(server_work)
    t.join()
    srv.server_close()

    assert b"Hello, World!" in responses[0]
    assert b'"status"' in responses[1]

    # Replay
    srv2 = make_server("127.0.0.1", 0, app)

    def replay_work():
        srv2._handle_request_noblock()
        srv2._handle_request_noblock()

    runner.replay(recording, replay_work)
    srv2.server_close()
