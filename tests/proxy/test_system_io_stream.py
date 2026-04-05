from contextlib import contextmanager
from dataclasses import dataclass

import pytest

import retracesoftware.stream as stream
from retracesoftware.proxy.tape import TapeReader, TapeWriter

from retracesoftware.proxy.io import recorder_context, replayer_context


@dataclass(frozen=True)
class IOMode:
    name: str
    debug: bool = False
    stacktraces: bool = False


IO_MODES = [
    pytest.param(IOMode("plain"), id="plain"),
    pytest.param(IOMode("stacktraces", stacktraces=True), id="stacktraces"),
]


def _configure_system(system):
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})


@contextmanager
def _recorder_context(mode, raw_writer):
    assert isinstance(raw_writer, TapeWriter)
    with recorder_context(
        tape_writer=raw_writer,
        debug=mode.debug,
        stacktraces=mode.stacktraces,
    ) as system:
        _configure_system(system)
        yield system


@contextmanager
def _replayer_context(mode, raw_reader):
    assert isinstance(raw_reader, TapeReader)
    with replayer_context(
        tape_reader=raw_reader,
        debug=mode.debug,
        stacktraces=mode.stacktraces,
    ) as system:
        _configure_system(system)
        yield system


def _thread_id():
    return "main-thread"


@pytest.mark.parametrize("mode", IO_MODES)
def test_system_io_round_trips_simple_patched_function_with_stream(mode, tmp_path):
    path = tmp_path / f"{mode.name}.bin"
    live_calls = []

    def add(a, b):
        live_calls.append((a, b))
        return a + b

    with stream.writer(
        path=path,
        thread=_thread_id,
        flush_interval=999,
        format="unframed_binary",
    ) as raw_writer:
        with _recorder_context(mode, raw_writer) as record_system:
            recorded_add = record_system.patch_function(add)
            with record_system.context():
                recorded = recorded_add(2, 3)

    assert recorded == 5
    assert live_calls == [(2, 3)]
    assert path.stat().st_size > 0

    with stream.reader(path=path, read_timeout=1, verbose=False, thread_id=_thread_id) as raw_reader:
        with _replayer_context(mode, raw_reader) as replay_system:
            replayed_add = replay_system.patch_function(add)
            with replay_system.context():
                replayed = replayed_add(2, 3)

    assert replayed == recorded
    assert live_calls == [(2, 3)]
