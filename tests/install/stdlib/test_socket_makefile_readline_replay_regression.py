"""Minimal in-process replay repros for server-side socket makefile reads."""

from __future__ import annotations

import gc
import socket
import threading

import pytest
from retracesoftware.testing.memorytape import record_then_replay


@pytest.fixture(scope="session", autouse=True)
def _install_runtime():
    yield None


def _configure_system(system):
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})


def _record_and_replay_via_io(fn, *, after_record=None, after_replay=None):
    result = record_then_replay(
        fn,
        configure_system=_configure_system,
        after_record=after_record,
        after_replay=after_replay,
        debug=False,
        stacktraces=False,
        inject_system=True,
    )
    return result.recorded, result.replayed, result.remaining


def test_socket_makefile_replay_leaves_buffered_reader_tail():
    """Minimal lower-level replay repro: makefile() leaves tape unconsumed."""

    def work(_system):
        left, right = socket.socketpair()
        rfile = None
        try:
            rfile = left.makefile("rb", buffering=8192)
            return type(rfile).__name__
        finally:
            if rfile is not None:
                rfile.close()
            left.close()
            right.close()

    recorded, replayed, remaining = _record_and_replay_via_io(
        work,
        after_record=gc.collect,
        after_replay=gc.collect,
    )

    gc.collect()

    assert recorded == "BufferedReader"
    assert replayed == "BufferedReader"
    assert remaining == []


def test_server_socket_makefile_readline_replays_recorded_request_line():
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

    def server_work(system):
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

    def run_record(system):
        client_thread = threading.Thread(
            target=system.disable_for(client),
            name="socket-makefile-unretraced-client",
            daemon=True,
        )
        try:
            client_thread.start()
            return server_work(system)
        finally:
            client_thread.join(timeout=5)
            assert not client_thread.is_alive(), "client thread failed to finish"

    recorded, replayed, remaining = _record_and_replay_via_io(run_record)
    assert recorded == request_line
    assert replayed == request_line
    assert remaining == []
