from contextlib import contextmanager
from dataclasses import dataclass

import pytest
import retracesoftware.utils as utils

from retracesoftware.proxy.io import recorder_context, replayer_context
from retracesoftware.testing.memorytape import MemoryTape


@dataclass(frozen=True)
class IOMode:
    name: str
    debug: bool = False
    stacktraces: bool = False


ALL_IO_MODES = [
    pytest.param(IOMode("plain"), id="plain"),
    pytest.param(IOMode("debug", debug=True), id="debug"),
    pytest.param(IOMode("stacktraces", stacktraces=True), id="stacktraces"),
    pytest.param(IOMode("debug-stacktraces", debug=True, stacktraces=True), id="debug-stacktraces"),
]

IO_MODES = ALL_IO_MODES


def _configure_system(system):
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})


@contextmanager
def _recorder_context(mode, writer):
    with recorder_context(
        tape_writer=writer,
        debug=mode.debug,
        stacktraces=mode.stacktraces,
    ) as system:
        _configure_system(system)
        yield system


@contextmanager
def _replayer_context(mode, reader):
    with replayer_context(
        tape_reader=reader,
        debug=mode.debug,
        stacktraces=mode.stacktraces,
    ) as system:
        _configure_system(system)
        yield system


def _contains_type_name(value, name):
    if type(value).__name__ == name:
        return True
    if isinstance(value, dict):
        return any(_contains_type_name(key, name) or _contains_type_name(item, name) for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_type_name(item, name) for item in value)
    return False


@pytest.mark.parametrize("mode", IO_MODES)
def test_system_io_round_trips_simple_patched_function_with_memory_tape(mode):
    tape = MemoryTape()
    writer = tape.writer()

    live_calls = []

    def add(a, b):
        live_calls.append((a, b))
        return a + b

    with _recorder_context(mode, writer) as record_system:
        recorded_add = record_system.patch_function(add)

        with record_system.context():
            recorded = recorded_add(2, 3)

    assert recorded == 5
    assert live_calls == [(2, 3)]
    assert "ON_START" in tape.tape
    if mode.stacktraces:
        assert "STACKTRACE" in tape.tape
    if mode.debug:
        assert "CHECKPOINT" in tape.tape
    else:
        assert "SYNC" in tape.tape
    assert "RESULT" in tape.tape
    assert tape.tape[-1] == "ON_END"

    reader = tape.reader()
    with _replayer_context(mode, reader) as replay_system:
        replayed_add = replay_system.patch_function(add)

        with replay_system.context():
            replayed = replayed_add(2, 3)

    assert replayed == recorded
    assert live_calls == [(2, 3)]


@pytest.mark.parametrize("mode", IO_MODES)
def test_system_io_round_trips_simple_override_callback_with_memory_tape(mode):
    tape = MemoryTape()
    writer = tape.writer()

    live_calls = []

    class Base:
        def trigger(self, value):
            live_calls.append(("external", value))
            return self.callback(value) + 1

        def callback(self, value):
            return 0

    class Sub(Base):
        def callback(self, value):
            live_calls.append(("callback", value))
            return value * 2

    with _recorder_context(mode, writer) as record_system:
        record_system.patch_type(Base)

        with record_system.context():
            recorded = Sub().trigger(5)

    assert recorded == 11
    assert live_calls == [("external", 5), ("callback", 5)]
    assert "CALL" in tape.tape
    assert "RESULT" in tape.tape

    reader = tape.reader()
    with _replayer_context(mode, reader) as replay_system:
        replay_system.patch_type(Base)

        with replay_system.context():
            replayed = Sub().trigger(5)

    assert replayed == recorded
    assert live_calls == [("external", 5), ("callback", 5), ("callback", 5)]


@pytest.mark.parametrize("mode", IO_MODES)
def test_system_io_records_and_rebinds_callback_receiver_with_memory_tape(mode):
    tape = MemoryTape()
    writer = tape.writer()

    callback_receivers = []

    class Base:
        def trigger(self, value):
            return self.callback(value)

        def callback(self, value):
            return 0

    class Sub(Base):
        def callback(self, value):
            callback_receivers.append(self)
            return value * 2

    with _recorder_context(mode, writer) as record_system:
        record_system.patch_type(Base)

        with record_system.context():
            recorded_obj = Sub()
            recorded = recorded_obj.trigger(7)

    assert recorded == 14
    assert callback_receivers == [recorded_obj]
    assert any(_contains_type_name(entry, "_BindingCreate") for entry in tape.tape)
    assert any(_contains_type_name(entry, "_BindingLookup") for entry in tape.tape)

    reader = tape.reader()
    with _replayer_context(mode, reader) as replay_system:
        replay_system.patch_type(Base)

        with replay_system.context():
            replay_obj = Sub()
            replayed = replay_obj.trigger(7)

    assert replayed == recorded
    assert callback_receivers == [recorded_obj, replay_obj]


@pytest.mark.parametrize("mode", IO_MODES)
def test_system_io_round_trips_dynamic_external_proxy_generation_with_memory_tape(mode):
    tape = MemoryTape()
    writer = tape.writer()

    class External:
        def ping(self):
            return "pong"

    class Example:
        def make_external(self):
            return External()

    with _recorder_context(mode, writer) as record_system:
        record_system.patch_type(Example)

        with record_system.context():
            recorded_root = Example()
            baseline_tape = len(tape.tape)
            recorded = recorded_root.make_external()

    assert isinstance(recorded, utils.ExternalWrapped)

    delta = tape.tape[baseline_tape:]
    call_indices = [index for index, value in enumerate(delta) if value == "CALL"]
    ext_proxy_calls = [
        index for index in call_indices
        if isinstance(delta[index + 3], dict)
        and delta[index + 3].get("module") == External.__module__
        and delta[index + 3].get("name") == External.__qualname__
        and "ping" in delta[index + 3].get("methods", ())
    ]
    assert ext_proxy_calls
    call_index = ext_proxy_calls[0]
    assert _contains_type_name(delta[call_index + 2], "_BindingLookup")
    assert delta[call_index + 3]["module"] == External.__module__
    assert delta[call_index + 3]["name"] == External.__qualname__
    assert "ping" in delta[call_index + 3]["methods"]
    assert any(_contains_type_name(entry, "_BindingCreate") for entry in delta)

    reader = tape.reader()
    with _replayer_context(mode, reader) as replay_system:
        replay_system.patch_type(Example)

        with replay_system.context():
            replay_root = Example()
            replayed = replay_root.make_external()

    assert isinstance(replayed, utils.ExternalWrapped)
    assert type(recorded) is not type(replayed)
    assert type(replayed).__name__ == External.__qualname__
