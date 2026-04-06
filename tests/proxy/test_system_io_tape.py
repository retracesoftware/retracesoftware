from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

import pytest
import retracesoftware.stream as stream
import retracesoftware.utils as utils

from retracesoftware.proxy.io import recorder_context, replayer_context
from retracesoftware.proxy.tape import Tape
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


def _thread_id():
    return "main-thread"


class StreamTape:
    def __init__(self, path: Path):
        self.path = path

    def writer(self):
        return stream.writer(
            path=self.path,
            thread=_thread_id,
            flush_interval=999,
            format="unframed_binary",
        )

    def reader(self):
        return stream.reader(
            path=self.path,
            read_timeout=1,
            verbose=False,
            thread_id=_thread_id,
        )


@pytest.fixture(params=["memory", "stream"], ids=["memory", "stream"])
def tape(request, tmp_path):
    if request.param == "memory":
        value = MemoryTape()
    else:
        value = StreamTape(tmp_path / "trace.bin")

    assert isinstance(value, Tape)
    return value


def _configure_system(system):
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})


@contextmanager
def _entered(resource):
    manager = resource if hasattr(resource, "__enter__") and hasattr(resource, "__exit__") else nullcontext(resource)
    with manager as entered:
        yield entered


@contextmanager
def _recorder_context(mode, tape_writer, gc_collect_multiplier=None):
    with recorder_context(
        tape_writer=tape_writer,
        debug=mode.debug,
        stacktraces=mode.stacktraces,
        gc_collect_multiplier=gc_collect_multiplier,
    ) as system:
        _configure_system(system)
        yield system


@contextmanager
def _replayer_context(mode, tape_reader):
    with replayer_context(
        tape_reader=tape_reader,
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


def _raw_tape(tape):
    return getattr(tape, "tape", None)


@pytest.mark.parametrize("mode", ALL_IO_MODES)
def test_system_io_round_trips_simple_patched_function_with_tape(mode, tape):
    writer = tape.writer()

    live_calls = []

    def add(a, b):
        live_calls.append((a, b))
        return a + b

    with _entered(writer) as writer:
        with _recorder_context(mode, writer) as record_system:
            recorded_add = record_system.patch_function(add)

            with record_system.context():
                recorded = recorded_add(2, 3)

    assert recorded == 5
    assert live_calls == [(2, 3)]

    raw = _raw_tape(tape)
    if raw is not None:
        assert "ON_START" in raw
        if mode.stacktraces:
            assert "STACKTRACE" in raw
        if mode.debug:
            assert "CHECKPOINT" in raw
        else:
            assert "SYNC" in raw
        assert "RESULT" in raw
        assert raw[-1] == "ON_END"
    elif hasattr(tape, "path"):
        assert tape.path.stat().st_size > 0

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(mode, reader) as replay_system:
            replayed_add = replay_system.patch_function(add)

            with replay_system.context():
                replayed = replayed_add(2, 3)

    assert replayed == recorded
    assert live_calls == [(2, 3)]


@pytest.mark.parametrize("mode", ALL_IO_MODES)
def test_system_io_round_trips_simple_override_callback_with_tape(mode, tape):
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

    with _entered(writer) as writer:
        with _recorder_context(mode, writer) as record_system:
            record_system.patch_type(Base)

            with record_system.context():
                recorded = Sub().trigger(5)

    assert recorded == 11
    assert live_calls == [("external", 5), ("callback", 5)]

    raw = _raw_tape(tape)
    if raw is not None:
        assert "CALL" in raw
        assert "RESULT" in raw

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(mode, reader) as replay_system:
            replay_system.patch_type(Base)

            with replay_system.context():
                replayed = Sub().trigger(5)

    assert replayed == recorded
    assert live_calls == [("external", 5), ("callback", 5), ("callback", 5)]


@pytest.mark.parametrize("mode", ALL_IO_MODES)
def test_system_io_records_and_rebinds_callback_receiver_with_tape(mode, tape):
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

    with _entered(writer) as writer:
        with _recorder_context(mode, writer) as record_system:
            record_system.patch_type(Base)

            with record_system.context():
                recorded_obj = Sub()
                recorded = recorded_obj.trigger(7)

    assert recorded == 14
    assert callback_receivers == [recorded_obj]

    raw = _raw_tape(tape)
    if raw is not None:
        assert any(_contains_type_name(entry, "_BindingCreate") for entry in raw)
        assert any(_contains_type_name(entry, "_BindingLookup") for entry in raw)

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(mode, reader) as replay_system:
            replay_system.patch_type(Base)

            with replay_system.context():
                replay_obj = Sub()
                replayed = replay_obj.trigger(7)

    assert replayed == recorded
    assert callback_receivers == [recorded_obj, replay_obj]


def test_system_io_replays_dynamic_internal_proxy_callback_side_effect_with_memory_tape():
    tape = MemoryTape()
    writer = tape.writer()

    class External:
        def run(self, callback_obj):
            return callback_obj.readable() + 1

    class Callback:
        def __init__(self, calls):
            self.calls = calls

        def readable(self):
            self.calls.append("called")
            return 10

    record_calls = []

    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            record_system.patch_type(External)

            with record_system.context():
                recorded = External().run(Callback(record_calls))

    assert recorded == 11
    assert record_calls == ["called"]

    reader = tape.reader()
    replay_calls = []
    with _entered(reader) as reader:
        with _replayer_context(IOMode("plain"), reader) as replay_system:
            replay_system.patch_type(External)

            with replay_system.context():
                replayed = External().run(Callback(replay_calls))

    assert replayed == recorded
    assert replay_calls == ["called"]


@pytest.mark.parametrize("mode", ALL_IO_MODES)
def test_system_io_round_trips_dynamic_external_proxy_generation_with_tape(mode, tape):
    writer = tape.writer()

    class External:
        def ping(self):
            return "pong"

    class Example:
        def make_external(self):
            return External()

    raw = _raw_tape(tape)

    with _entered(writer) as writer:
        with _recorder_context(mode, writer) as record_system:
            record_system.patch_type(Example)

            with record_system.context():
                recorded_root = Example()
                baseline_tape = len(raw) if raw is not None else None
                recorded = recorded_root.make_external()

    assert isinstance(recorded, utils.ExternalWrapped)

    if raw is not None:
        delta = raw[baseline_tape:]
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
    with _entered(reader) as reader:
        with _replayer_context(mode, reader) as replay_system:
            replay_system.patch_type(Example)

            with replay_system.context():
                replay_root = Example()
                replayed = replay_root.make_external()

    assert isinstance(replayed, utils.ExternalWrapped)
    assert type(recorded) is not type(replayed)
    assert type(replayed).__name__ == External.__qualname__


@pytest.mark.parametrize("mode", [pytest.param(IOMode("plain"), id="plain")])
def test_system_io_records_gc_collect_as_async_call_with_tape(mode, tape):
    writer = tape.writer()

    live_calls = []

    def add(a, b):
        live_calls.append((a, b))
        return a + b

    with _entered(writer) as writer:
        with _recorder_context(mode, writer, gc_collect_multiplier=1 << 20) as record_system:
            recorded_add = record_system.patch_function(add)

            with record_system.context():
                recorded = recorded_add(2, 3)

    assert recorded == 5
    assert live_calls == [(2, 3)]

    raw = _raw_tape(tape)
    if raw is not None:
        call_records = [
            (raw[index + 1], raw[index + 2], raw[index + 3])
            for index, value in enumerate(raw)
            if value == "CALL"
        ]
        assert len(call_records) == 1
        _, args, kwargs = call_records[0]
        assert isinstance(args, tuple)
        assert len(args) == 1
        assert args[0] in (0, 1, 2)
        assert kwargs == {}
        assert "RESULT" in raw

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(mode, reader) as replay_system:
            replayed_add = replay_system.patch_function(add)

            with replay_system.context():
                replayed = replayed_add(2, 3)

    assert replayed == recorded
    assert live_calls == [(2, 3)]
