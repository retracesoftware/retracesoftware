from dataclasses import dataclass

import pytest
import retracesoftware.utils as utils

from retracesoftware.proxy.io import IO
from retracesoftware.proxy.system import System
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


def _make_io(system, mode):
    return IO(system, debug=mode.debug, stacktraces=mode.stacktraces)


def _contains_type_name(value, name):
    if type(value).__name__ == name:
        return True
    if isinstance(value, dict):
        return any(_contains_type_name(key, name) or _contains_type_name(item, name) for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_type_name(item, name) for item in value)
    return False


def _restore_bound_snapshot(system, snapshot):
    keep_ids = {id(obj) for obj in snapshot}
    for obj in tuple(system.is_bound.ordered()):
        if id(obj) not in keep_ids:
            system.is_bound.discard(obj)


@pytest.mark.parametrize("mode", IO_MODES)
def test_system_io_round_trips_simple_patched_function_with_memory_tape(mode):
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    tape = MemoryTape()
    writer = tape.writer()

    live_calls = []

    def add(a, b):
        live_calls.append((a, b))
        return a + b

    patched_add = system.patch_function(add)

    with _make_io(system, mode).writer(writer.write, writer.bind):
        recorded = patched_add(2, 3)

    assert recorded == 5
    assert live_calls == [(2, 3)]
    assert tape.tape[0] == "ON_START"
    if mode.stacktraces:
        assert "STACKTRACE" in tape.tape
    if mode.debug:
        assert "CHECKPOINT" in tape.tape
    else:
        assert "SYNC" in tape.tape
    assert "RESULT" in tape.tape
    assert tape.tape[-1] == "ON_END"

    reader = tape.reader()

    with _make_io(system, mode).reader(reader.read, reader.bind):
        replayed = patched_add(2, 3)

    assert replayed == recorded
    assert live_calls == [(2, 3)]


@pytest.mark.parametrize("mode", IO_MODES)
def test_system_io_round_trips_simple_override_callback_with_memory_tape(mode):
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    tape = MemoryTape()
    writer = tape.writer()

    live_calls = []

    class Base:
        def trigger(self, value):
            live_calls.append(("external", value))
            return self.callback(value) + 1

        def callback(self, value):
            return 0

    system.patch_type(Base)

    class Sub(Base):
        def callback(self, value):
            live_calls.append(("callback", value))
            return value * 2

    with _make_io(system, mode).writer(writer.write, writer.bind):
        recorded = Sub().trigger(5)

    assert recorded == 11
    assert live_calls == [("external", 5), ("callback", 5)]
    assert "CALL" in tape.tape
    assert "RESULT" in tape.tape

    reader = tape.reader()

    with _make_io(system, mode).reader(reader.read, reader.bind):
        replayed = Sub().trigger(5)

    assert replayed == recorded
    assert live_calls == [("external", 5), ("callback", 5), ("callback", 5)]


@pytest.mark.parametrize("mode", IO_MODES)
def test_system_io_records_and_rebinds_callback_receiver_with_memory_tape(mode):
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    tape = MemoryTape()
    writer = tape.writer()

    callback_receivers = []

    class Base:
        def trigger(self, value):
            return self.callback(value)

        def callback(self, value):
            return 0

    system.patch_type(Base)

    class Sub(Base):
        def callback(self, value):
            callback_receivers.append(self)
            return value * 2

    with _make_io(system, mode).writer(writer.write, writer.bind):
        recorded_obj = Sub()
        recorded = recorded_obj.trigger(7)

    assert recorded == 14
    assert callback_receivers == [recorded_obj]
    assert any(_contains_type_name(entry, "_BindingCreate") for entry in tape.tape)
    assert any(_contains_type_name(entry, "_BindingLookup") for entry in tape.tape)

    reader = tape.reader()

    with _make_io(system, mode).reader(reader.read, reader.bind):
        replay_obj = Sub()
        replayed = replay_obj.trigger(7)

    assert replayed == recorded
    assert callback_receivers == [recorded_obj, replay_obj]


@pytest.mark.parametrize("mode", IO_MODES)
def test_system_io_round_trips_dynamic_external_proxy_generation_with_memory_tape(mode):
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    tape = MemoryTape()
    writer = tape.writer()

    class External:
        def ping(self):
            return "pong"

    class Example:
        def make_external(self):
            return External()

    system.patch_type(Example)
    baseline_bound = tuple(system.is_bound.ordered())

    with _make_io(system, mode).writer(writer.write, writer.bind):
        recorded_root = Example()
        baseline_tape = len(tape.tape)
        recorded = recorded_root.make_external()

    assert isinstance(recorded, utils.ExternalWrapped)

    delta = tape.tape[baseline_tape:]
    call_indices = [index for index, value in enumerate(delta) if value == "CALL"]
    ext_proxy_calls = [
        index for index in call_indices
        if getattr(delta[index + 1], "__name__", None) in {"ext_proxytype_from_spec", "_ext_proxytype_from_spec"}
    ]
    assert ext_proxy_calls
    call_index = ext_proxy_calls[0]
    assert delta[call_index + 2] == (system,)
    assert delta[call_index + 3]["module"] == External.__module__
    assert delta[call_index + 3]["name"] == External.__qualname__
    assert "ping" in delta[call_index + 3]["methods"]
    assert any(_contains_type_name(entry, "_BindingCreate") for entry in delta)

    _restore_bound_snapshot(system, baseline_bound)
    reader = tape.reader()

    with _make_io(system, mode).reader(reader.read, reader.bind):
        replay_root = Example()
        replayed = replay_root.make_external()

    assert isinstance(replayed, utils.ExternalWrapped)
    assert type(recorded) is not type(replayed)
    assert type(replayed).__name__ == External.__qualname__
