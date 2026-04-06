from argparse import Namespace
import time

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


def test_install_and_run_reads_socket_family_with_memory_tape():
    import _socket

    tape = MemoryTape()

    record_system = recorder(
        tape_writer=tape.writer(),
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
