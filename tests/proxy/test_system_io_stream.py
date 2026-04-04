from dataclasses import dataclass

import pytest

import retracesoftware.stream as stream

from retracesoftware.proxy.io import IO
from retracesoftware.proxy.system import System


@dataclass(frozen=True)
class IOMode:
    name: str
    debug: bool = False
    stacktraces: bool = False


IO_MODES = [
    pytest.param(IOMode("plain"), id="plain"),
    pytest.param(IOMode("stacktraces", stacktraces=True), id="stacktraces"),
]


def _make_io(system, mode):
    return IO(system, debug=mode.debug, stacktraces=mode.stacktraces)


def _thread_id():
    return "main-thread"


@pytest.mark.parametrize("mode", IO_MODES)
def test_system_io_round_trips_simple_patched_function_with_stream(mode, tmp_path):
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    path = tmp_path / f"{mode.name}.bin"
    live_calls = []

    def add(a, b):
        live_calls.append((a, b))
        return a + b

    patched_add = system.patch_function(add)

    with stream.writer(
        path=path,
        thread=_thread_id,
        flush_interval=999,
        format="unframed_binary",
    ) as raw_writer:
        with _make_io(system, mode).writer(raw_writer, raw_writer.bind):
            recorded = patched_add(2, 3)

    assert recorded == 5
    assert live_calls == [(2, 3)]
    assert path.stat().st_size > 0

    with stream.reader(path=path, read_timeout=1, verbose=False, thread_id=_thread_id) as raw_reader:
        with _make_io(system, mode).reader(raw_reader.next, raw_reader.bind):
            replayed = patched_add(2, 3)

    assert replayed == recorded
    assert live_calls == [(2, 3)]
