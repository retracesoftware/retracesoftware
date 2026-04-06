from argparse import Namespace
import gc
import time
import pytest

from retracesoftware.__main__ import install_and_run
from retracesoftware.proxy.io import recorder, replayer
from retracesoftware.testing.memorytape import MemoryTape


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


def test_install_and_run_round_trips_time_proxy_with_memory_tape(monkeypatch):
    tape = MemoryTape()
    live_calls = []

    def fake_time():
        live_calls.append("time")
        return 123.456

    monkeypatch.setattr(time, "time", fake_time)

    record_system = recorder(
        tape_writer=tape.writer(),
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
    assert "ON_END" in tape.tape

    replay_system = replayer(
        tape_reader=tape.reader(),
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


@pytest.mark.xfail(strict=True, reason="Server-side socket.makefile replay still diverges on the install_and_run + MemoryTape seam")
def test_install_and_run_socket_makefile_readline_currently_diverges_with_memory_tape():
    from retracesoftware.proxy.io import ReplayException

    tape = MemoryTape()

    import queue
    import socket
    import threading

    request_line = b"GET /health HTTP/1.1\r\n"
    request_bytes = request_line + b"Host: localhost\r\n\r\n"

    def send_request(port):
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.connect(("127.0.0.1", port))
            client.sendall(request_bytes)
        finally:
            client.close()

    def make_socket_roundtrip(port_queue):
        def run():
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(("127.0.0.1", 0))
            server_socket.listen(1)
            port_queue.put(server_socket.getsockname()[1])

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

        return run

    record_system = recorder(
        tape_writer=tape.writer(),
        debug=False,
        stacktraces=False,
    )
    _configure_system(record_system)

    record_port_queue = queue.Queue()

    def record_client():
        send_request(record_port_queue.get())

    record_thread = threading.Thread(target=record_client)
    record_system.disable_for(record_thread.start)()

    recorded = install_and_run(
        system=record_system,
        options=_options(),
        function=make_socket_roundtrip(record_port_queue),
    )

    record_thread.join(timeout=5)
    assert not record_thread.is_alive(), "client thread hung"
    assert recorded == request_line

    replay_system = replayer(
        tape_reader=tape.reader(),
        debug=False,
        stacktraces=False,
    )
    _configure_system(replay_system)

    with pytest.raises((RuntimeError, ReplayException)) as exc_info:
        install_and_run(
            system=replay_system,
            options=_options(),
            function=make_socket_roundtrip(queue.Queue()),
        )

    message = str(exc_info.value)
    del exc_info
    del replay_system
    del record_system
    gc.collect()

    assert (
        message.startswith("expected BindingCreate, got ")
        or message.startswith("Unexpected message: ")
    )
