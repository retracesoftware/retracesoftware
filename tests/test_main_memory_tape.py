from argparse import Namespace
import os
import time

import pytest

from retracesoftware.__main__ import install_and_run
from retracesoftware.proxy.io import recorder, replayer
from retracesoftware.testing.memorytape import IOMemoryTape, record_then_replay
from tests.runner import retrace_test


def _options(**overrides):
    values = dict(
        monitor=0,
        retrace_file_patterns=None,
        verbose=False,
        trace_shutdown=False,
    )
    values.update(overrides)
    return Namespace(**values)


def _configure_system(system):
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})


def test_record_then_replay_helper_round_trips_time_proxy(monkeypatch):
    live_calls = []

    def fake_time():
        live_calls.append("time")
        return 123.456

    monkeypatch.setattr(time, "time", fake_time)

    result = record_then_replay(
        lambda: time.time(),
        configure_system=_configure_system,
    )

    assert result.recorded == 123.456
    assert result.replayed == 123.456
    assert result.remaining == []
    assert live_calls == ["time"]


@retrace_test
def test_retrace_test_pytest_smoke():
    import socket

    value = socket.gethostname()
    assert isinstance(value, str)
    assert value


def test_install_and_run_round_trips_time_proxy_with_memory_tape(monkeypatch):
    tape = IOMemoryTape()
    live_calls = []

    def fake_time():
        live_calls.append("time")
        return 123.456

    monkeypatch.setattr(time, "time", fake_time)

    record_system = recorder(
        writer=tape.writer().write,
        debug=False,
        stacktraces=False,
    )
    _configure_system(record_system)

    recorded = install_and_run(
        system=record_system,
        options=_options(),
        function=lambda: time.time(),
    )

    assert recorded == 123.456
    assert live_calls == ["time"]
    assert time.time is fake_time
    assert "ON_START" in tape.tape

    replay_reader = tape.reader()
    replay_system = replayer(
        next_object=replay_reader.read,
        close=getattr(replay_reader, "close", None),
        debug=False,
        stacktraces=False,
    )
    _configure_system(replay_system)

    replayed = install_and_run(
        system=replay_system,
        options=_options(),
        function=lambda: time.time(),
    )

    assert replayed == recorded
    assert live_calls == ["time"]
    assert time.time is fake_time


def test_install_and_run_round_trips_os_chdir_error_with_memory_tape(tmp_path):
    tape = IOMemoryTape()
    missing = tmp_path / "missing-dir"

    record_system = recorder(
        writer=tape.writer().write,
        debug=False,
        stacktraces=False,
    )
    _configure_system(record_system)

    with pytest.raises(FileNotFoundError, match="missing-dir"):
        install_and_run(
            system=record_system,
            options=_options(),
            function=lambda: os.chdir(missing),
        )

    replay_reader = tape.reader()
    replay_system = replayer(
        next_object=replay_reader.read,
        close=getattr(replay_reader, "close", None),
        debug=False,
        stacktraces=False,
    )
    _configure_system(replay_system)

    with pytest.raises(FileNotFoundError, match="missing-dir"):
        install_and_run(
            system=replay_system,
            options=_options(),
            function=lambda: os.chdir(missing),
        )


def test_install_and_run_reads_socket_family_with_memory_tape():
    import _socket

    tape = IOMemoryTape()

    record_system = recorder(
        writer=tape.writer().write,
        debug=False,
        stacktraces=False,
    )
    _configure_system(record_system)

    def read_family():
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            return sock.family
        finally:
            sock.close()

    result = install_and_run(
        system=record_system,
        options=_options(),
        function=read_family,
    )

    assert result == _socket.AF_INET


def test_install_and_run_round_trips_allocate_lock_with_memory_tape():
    import _thread

    tape = IOMemoryTape()

    def allocate_and_check():
        lock = _thread.allocate_lock()
        assert lock.acquire(False)
        try:
            return lock.locked()
        finally:
            lock.release()

    record_system = recorder(
        writer=tape.writer().write,
        debug=False,
        stacktraces=False,
    )
    _configure_system(record_system)

    recorded = install_and_run(
        system=record_system,
        options=_options(),
        function=allocate_and_check,
    )

    replay_reader = tape.reader()
    replay_system = replayer(
        next_object=replay_reader.read,
        close=getattr(replay_reader, "close", None),
        debug=False,
        stacktraces=False,
    )
    _configure_system(replay_system)

    replayed = install_and_run(
        system=replay_system,
        options=_options(),
        function=allocate_and_check,
    )

    assert recorded is True
    assert replayed is recorded


def test_install_and_run_replays_flask_request_from_unretraced_client_thread_with_memory_tape():
    pytest.importorskip("flask")

    import queue
    import socket
    import threading
    from flask import Flask
    from wsgiref.simple_server import make_server

    tape = IOMemoryTape()
    path = "/hello"
    body = b"Hello from Flask!"

    def http_get(port, path):
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.connect(("127.0.0.1", port))
            client.sendall(
                f"GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n".encode("ascii")
            )
            chunks = []
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            client.close()

    def make_roundtrip(port_queue, hits):
        app = Flask(__name__)

        @app.route(path)
        def hello():
            hits.append("hello")
            return body.decode("ascii")

        server = make_server("127.0.0.1", 0, app)
        server.socket.settimeout(2.0)
        port_queue.put(server.server_address[1])
        try:
            server._handle_request_noblock()
            return list(hits)
        finally:
            server.server_close()

    record_system = recorder(
        writer=tape.writer().write,
        debug=False,
        stacktraces=True,
    )
    _configure_system(record_system)

    record_port_queue = queue.Queue()
    record_result = {}

    def record_client():
        record_result["response"] = http_get(record_port_queue.get(timeout=30), path)

    record_thread = threading.Thread(target=record_client)
    record_system.disable_for(record_thread.start)()

    recorded_hits = install_and_run(
        system=record_system,
        options=_options(),
        function=make_roundtrip,
        args=(record_port_queue, []),
    )

    record_thread.join(timeout=5)
    assert not record_thread.is_alive(), "client thread hung"
    assert recorded_hits == ["hello"]
    assert body in record_result["response"]

    replay_reader = tape.reader()
    replay_system = replayer(
        next_object=replay_reader.read,
        close=getattr(replay_reader, "close", None),
        debug=False,
        stacktraces=True,
    )
    _configure_system(replay_system)

    replayed_hits = install_and_run(
        system=replay_system,
        options=_options(),
        function=make_roundtrip,
        args=(queue.Queue(), []),
    )

    assert replayed_hits == ["hello"]
