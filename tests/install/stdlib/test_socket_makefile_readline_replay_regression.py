"""Minimal in-process replay repro for server-side socket makefile reads.

This keeps the same install-for-pytest harness shape used in the Flask
investigation, but strips everything down to the smallest server-side path
that still fails today:

- create the server socket inside ``runner.record(...)`` / ``runner.replay(...)``
- accept one real client connection during record
- wrap the accepted socket with ``makefile("rb", buffering=8192)``
- read one HTTP request line via ``readline(65537)``

Current failure signature:
- record returns the first request line correctly
- replay diverges inside the buffered file path before returning that line
"""

from __future__ import annotations

import socket
import threading

import pytest


@pytest.fixture
def socket_runtime(runner, system):
    return runner, system


def test_server_socket_makefile_readline_replays_recorded_request_line(socket_runtime):
    runner, system = socket_runtime

    ready = threading.Event()
    port_box: list[int] = []
    request_line = b"GET /health HTTP/1.1\r\n"
    request_bytes = request_line + b"Host: localhost\r\n\r\n"

    def client():
        ready.wait(timeout=2.0)
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client_socket.connect(("127.0.0.1", port_box[0]))
            client_socket.sendall(request_bytes)
        finally:
            client_socket.close()

    def server_work():
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("127.0.0.1", 0))
        port_box.append(server_socket.getsockname()[1])
        server_socket.listen(1)
        ready.set()

        conn = None
        rfile = None
        try:
            conn, _client_addr = server_socket.accept()
            rfile = conn.makefile("rb", buffering=8192)
            return rfile.readline(65537)
        finally:
            if rfile is not None:
                rfile.close()
            if conn is not None:
                conn.close()
            server_socket.close()

    client_thread = threading.Thread(
        target=system.disable_for(client),
        name="socket-makefile-unretraced-client",
        daemon=True,
    )

    try:
        client_thread.start()
        recording = runner.record(server_work)
    finally:
        client_thread.join(timeout=5)
        assert not client_thread.is_alive(), "client thread failed to finish"

    assert recording.result == request_line

    replay_result = runner.replay(recording, server_work)

    assert replay_result == request_line
