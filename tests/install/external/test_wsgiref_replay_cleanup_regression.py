"""Regression: replay leaves WSGI/socket cleanup tape unconsumed.

Root component focus:
- replay-side consumption of socket/file-object cleanup after handling WSGI
  requests via ``wsgiref.simple_server``
- lower-level socket cleanup rather than Flask application logic

Ownership signal:
- a plain stdlib WSGI app reproduces the same cleanup tail seen in the Flask
  repro
- the single-request case fails below Flask itself, in shared WSGI/socket
  cleanup
"""

from __future__ import annotations

import signal
import socket
import threading

import retracesoftware.functional as functional
from retracesoftware.install.startthread import patch_thread_start
from retracesoftware.testing.protocol_memory import MemoryReader
from wsgiref.simple_server import make_server


def _http_get(port: int, path: str) -> bytes:
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


def _replay_with_tail_debug(runner, recording, fn):
    reader = MemoryReader(recording.tape, timeout=10, monitor_enabled=False)
    context = runner._system.replay_context(
        reader,
        callback_normalize=(
            runner._install_session.normalize_replay_callback
            if runner._install_session is not None
            else None
        ),
    )

    def wrapper(target):
        def wrapped(*a, **kw):
            with context:
                return target(*a, **kw)

        return wrapped

    dispatch = runner._system.create_dispatch(
        external=wrapper,
        internal=functional.identity,
        disabled=functional.identity,
    )
    patch_thread_start(dispatch)

    replay_exc = None
    replay_result = None

    try:
        with context:
            if runner._install_session is not None:
                runner._install_session.activate_callback_binding(runner._system.bind)
            try:
                replay_result = fn()
            except Exception as exc:  # noqa: BLE001 - want exact replay exception
                replay_exc = exc
            finally:
                if runner._install_session is not None:
                    runner._install_session.deactivate_callback_binding()
    finally:
        pos = reader._source._pos
        next_entries = [repr(item) for item in recording.tape[pos:pos + 12]]

    return replay_result, replay_exc, pos, reader.remaining, next_entries


def test_wsgiref_single_request_replay_consumes_cleanup_tape(runner):
    def app(environ, start_response):
        body = b"Hello, World!"
        start_response(
            "200 OK",
            [
                ("Content-Type", "text/plain"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    srv = make_server("127.0.0.1", 0, app)
    port = srv.server_address[1]
    response_raw = [None]
    connected = threading.Event()

    def client():
        connected.set()
        response_raw[0] = _http_get(port, "/hello")

    t = threading.Thread(target=client)
    t.start()
    connected.wait(timeout=2)

    def server_work():
        srv._handle_request_noblock()

    recording = runner.record(server_work)
    t.join(timeout=5)
    srv.server_close()

    assert recording.error is None, recording.error
    assert b"Hello, World!" in (response_raw[0] or b"")

    srv2 = make_server("127.0.0.1", 0, app)

    def replay_work():
        srv2._handle_request_noblock()

    def _timeout_handler(signum, frame):
        raise TimeoutError("wsgiref replay timed out after 15s")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(15)
    try:
        replay_result, replay_exc, reader_pos, leftover, next_entries = (
            _replay_with_tail_debug(runner, recording, replay_work)
        )
    finally:
        signal.alarm(0)
        srv2.server_close()

    assert replay_exc is None, (
        "plain wsgiref replay raised unexpectedly\n"
        f"exc: {type(replay_exc).__name__}: {replay_exc}\n"
        f"reader_pos: {reader_pos}\n"
        f"tape_len: {len(recording.tape)}\n"
        f"remaining: {leftover}\n"
        f"next_entries:\n" + "\n".join(next_entries)
    )
    assert replay_result == recording.result
    assert leftover == 0, (
        "plain wsgiref replay left unconsumed tape entries\n"
        f"reader_pos: {reader_pos}\n"
        f"tape_len: {len(recording.tape)}\n"
        f"remaining: {leftover}\n"
        f"next_entries:\n" + "\n".join(next_entries)
    )
