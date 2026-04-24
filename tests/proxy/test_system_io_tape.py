from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

import pytest
import retracesoftware.stream as stream

from retracesoftware.proxy.io import (
    _ThreadDemuxSource,
    _IoMessageSource,
    _RawTapeSource,
    _ReplayBindingState,
    recorder_context,
    replayer,
)
from retracesoftware.proxy.system import ProxyRef
from retracesoftware.proxy.tape import Tape
from retracesoftware.tape import RawTapeWriter
from retracesoftware.testing.memorytape import IOMemoryTape
import retracesoftware.utils as utils


@dataclass(frozen=True)
class IOMode:
    name: str
    debug: bool = False
    stacktraces: bool = False


ALL_IO_MODES = [
    pytest.param(IOMode("plain"), id="plain"),
    pytest.param(IOMode("debug", debug=True), id="debug"),
    pytest.param(IOMode("stacktraces", stacktraces=True), id="stacktraces"),
    pytest.param(
        IOMode("debug-stacktraces", debug=True, stacktraces=True),
        marks=pytest.mark.skip(reason="proxy.io debug+stacktraces raw-tape replay is stale against current System.run"),
        id="debug-stacktraces",
    ),
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
        return _RawStreamTapeReader(self.path)


class _RawStreamTapeReader:
    __slots__ = ("_tape_reader",)

    def __init__(self, path: Path):
        self._tape_reader = stream.TapeReader(
            path=path,
            read_timeout=1,
            verbose=False,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._tape_reader.close()

    def read(self):
        value = self._tape_reader.next()
        while isinstance(value, stream.Heartbeat):
            value = self._tape_reader.next()
        return value


class FreshExternalResult:
    def ping(self):
        return "pong"


def make_fresh_external_result():
    return FreshExternalResult()


@pytest.fixture(params=["memory", "stream"], ids=["memory", "stream"])
def tape(request, tmp_path):
    if request.param == "memory":
        value = IOMemoryTape()
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
        writer=tape_writer.write,
        debug=mode.debug,
        stacktraces=mode.stacktraces,
        gc_collect_multiplier=gc_collect_multiplier,
    ) as system:
        _configure_system(system)
        yield system


@contextmanager
def _replayer_context(mode, tape_reader):
    system = replayer(
        next_object=tape_reader.read,
        close=getattr(tape_reader, "close", None),
        debug=mode.debug,
        stacktraces=mode.stacktraces,
    )
    try:
        _configure_system(system)
        yield system
    finally:
        system.unpatch_types()


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


def test_replayer_consumes_protocol_result_after_thread_switch():
    class FlatTapeReader:
        def __init__(self, items):
            self._iter = iter(items)

        def read(self):
            return next(self._iter)

        def close(self):
            return None

    message = _IoMessageSource(
        _ReplayBindingState(
            _ThreadDemuxSource(
                FlatTapeReader([
                    "THREAD_SWITCH",
                    0,
                    "RESULT",
                    42,
                ]).read,
                thread_id=lambda: 0,
                initial_thread_id=0,
            )
        ).read,
        thread_id=lambda: 0,
    ).read()

    assert message.result == 42
    assert message.thread_id == 0


def test_replay_binding_state_consumes_trailing_binding_deletes():
    class FlatTapeReader:
        def __init__(self, items):
            self._iter = iter(items)

        def read(self):
            return next(self._iter)

    reader = _ReplayBindingState(
        FlatTapeReader([
            (stream._BIND_CLOSE_TAG, 7),
            (stream._BIND_CLOSE_TAG, 8),
            "RESULT",
        ])
    )
    reader._bindings = {7: object(), 8: object()}

    reader.consume_pending_closes()

    assert reader._bindings == {}
    assert reader.read() == "RESULT"


def test_replay_binding_state_hydrates_proxy_ref_bindings():
    class DemoExternalWrapped(utils.ExternalWrapped):
        pass

    reader = _ReplayBindingState(iter(()).__next__)
    reader.bind_handle(7, ProxyRef(DemoExternalWrapped))

    resolved = reader.resolve({"value": [stream.Binding(7)]})

    assert isinstance(resolved["value"][0], DemoExternalWrapped)


def test_raw_tape_source_consumes_binding_delete_before_thread_demux():
    source = _RawTapeSource(
        iter([
            "THREAD_SWITCH",
            1,
            "BINDING_DELETE",
            7,
            "THREAD_SWITCH",
            0,
            "RESULT",
            42,
        ]).__next__
    )
    deleted = []
    source._on_bind_close = deleted.append

    demux = _ThreadDemuxSource(
        source.read,
        thread_id=lambda: 0,
        initial_thread_id=0,
    )

    assert demux.read() == "RESULT"
    assert deleted == [7]


def test_raw_tape_writer_passes_binding_objects_through_to_stream_writer():
    class FakeTapeWriter:
        def __init__(self):
            self.written = []

        def write(self, *values):
            self.written.append(values)

    writer = FakeTapeWriter()
    raw_writer = RawTapeWriter(writer)
    binding = stream.Binding(7)

    raw_writer.write("RESULT", {"value": [binding]})

    assert writer.written == [
        ("RESULT", {"value": [binding]}),
    ]


def test_system_io_records_callback_for_new_ext_proxy_type_with_memory_tape():
    tape = IOMemoryTape()
    writer = tape.writer()

    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            recorded_make = record_system.patch_function(make_fresh_external_result)
            record_system.run(recorded_make)

    raw = _raw_tape(tape)

    assert raw is not None
    assert "CALL" in raw
    assert "CALLBACK" in raw
    assert "RESULT" in raw


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
            recorded = record_system.run(recorded_add, 2, 3)

    assert recorded == 5
    assert live_calls == [(2, 3)]

    raw = _raw_tape(tape)
    if raw is not None:
        assert "ON_START" in raw
        if mode.debug:
            assert "CHECKPOINT" in raw
        else:
            assert "CALL" in raw
        assert "RESULT" in raw
    elif hasattr(tape, "path"):
        assert tape.path.stat().st_size > 0

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(mode, reader) as replay_system:
            replayed_add = replay_system.patch_function(add)
            replayed = replay_system.run(replayed_add, 2, 3)

    assert replayed == recorded
    assert live_calls == [(2, 3)]


def test_system_io_round_trips_simple_patched_function_error_with_memory_tape():
    tape = IOMemoryTape()
    writer = tape.writer()

    live_calls = []

    def fail(path):
        live_calls.append(path)
        raise FileNotFoundError(path)

    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            recorded_fail = record_system.patch_function(fail)
            with pytest.raises(FileNotFoundError, match="missing-path"):
                record_system.run(recorded_fail, "missing-path")

    assert live_calls == ["missing-path"]

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(IOMode("plain"), reader) as replay_system:
            replayed_fail = replay_system.patch_function(fail)
            with pytest.raises(FileNotFoundError, match="missing-path"):
                replay_system.run(replayed_fail, "missing-path")

    assert live_calls == ["missing-path"]


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

            def trigger(cls, value):
                return cls().trigger(value)

            recorded = record_system.run(trigger, Sub, 5)

    assert recorded == 11
    assert live_calls == [("external", 5), ("callback", 5)]

    raw = _raw_tape(tape)
    if raw is not None:
        if mode.debug:
            assert "CHECKPOINT" in raw
        else:
            assert "CALL" in raw
        assert "RESULT" in raw

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(mode, reader) as replay_system:
            replay_system.patch_type(Base)

            def trigger(cls, value):
                return cls().trigger(value)

            replayed = replay_system.run(trigger, Sub, 5)

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
            recorded_state = {}

            def do_record(cls, value):
                recorded_state["obj"] = cls()
                return recorded_state["obj"].trigger(value)

            recorded = record_system.run(do_record, Sub, 7)
            recorded_obj = recorded_state["obj"]

    assert recorded == 14
    assert callback_receivers == [recorded_obj]

    raw = _raw_tape(tape)
    if raw is not None:
        assert "NEW_BINDING" in raw

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(mode, reader) as replay_system:
            replay_system.patch_type(Base)
            replay_state = {}

            def do_replay(cls, value):
                replay_state["obj"] = cls()
                return replay_state["obj"].trigger(value)

            replayed = replay_system.run(do_replay, Sub, 7)
            replay_obj = replay_state["obj"]

    assert replayed == recorded
    assert callback_receivers == [recorded_obj, replay_obj]


def test_system_io_round_trips_nested_callback_external_call_with_memory_tape():
    tape = IOMemoryTape()
    writer = tape.writer()

    live_calls = []
    current_clock = None

    def clock():
        live_calls.append("clock")
        return 100

    class Base:
        def trigger(self, value):
            live_calls.append(("external", value))
            return self.callback(value) + 1

        def callback(self, value):
            return 0

    class Sub(Base):
        def callback(self, value):
            live_calls.append(("callback", value))
            return value + current_clock()

    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            record_system.patch_type(Base)
            current_clock = record_system.patch_function(clock)
            recorded = record_system.run(lambda cls, value: cls().trigger(value), Sub, 5)

    assert recorded == 106
    assert live_calls == [("external", 5), ("callback", 5), "clock"]

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(IOMode("plain"), reader) as replay_system:
            replay_system.patch_type(Base)
            current_clock = replay_system.patch_function(clock)
            replayed = replay_system.run(lambda cls, value: cls().trigger(value), Sub, 5)

    assert replayed == recorded
    assert live_calls == [("external", 5), ("callback", 5), "clock", ("callback", 5)]


def test_system_io_replays_dynamic_internal_proxy_callback_side_effect_with_memory_tape():
    tape = IOMemoryTape()
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

            def run_callback(external_cls, callback_cls, calls):
                return external_cls().run(callback_cls(calls))

            recorded = record_system.run(run_callback, External, Callback, record_calls)

    assert recorded == 11
    assert record_calls == ["called"]

    reader = tape.reader()
    replay_calls = []
    with _entered(reader) as reader:
        with _replayer_context(IOMode("plain"), reader) as replay_system:
            replay_system.patch_type(External)

            def run_callback(external_cls, callback_cls, calls):
                return external_cls().run(callback_cls(calls))

            replayed = replay_system.run(run_callback, External, Callback, replay_calls)

    assert replayed == recorded
    assert replay_calls == ["called"]


def test_system_io_round_trips_external_result_proxy_hydration_with_memory_tape():
    tape = IOMemoryTape()
    writer = tape.writer()

    make_calls = []
    current_factory = None

    def make_result():
        make_calls.append("make")
        return FreshExternalResult()

    def run_flow():
        return current_factory().ping()

    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            current_factory = record_system.patch_function(make_result)
            recorded = record_system.run(run_flow)

    assert recorded == "pong"
    assert make_calls == ["make"]

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(IOMode("plain"), reader) as replay_system:
            current_factory = replay_system.patch_function(make_result)
            replayed = replay_system.run(run_flow)

    assert replayed == recorded


def test_system_ext_proxytype_can_build_for_socket_family_descriptor_type():
    import _socket
    import os
    from retracesoftware.proxy.system import System

    pid = os.fork()
    if pid == 0:
        system = System()
        _configure_system(system)
        descriptor_type = type(_socket.socket.__dict__["family"])
        system.ext_proxytype(descriptor_type)
        os._exit(0)

    _, status = os.waitpid(pid, 0)

    assert os.WIFEXITED(status)
    assert os.WEXITSTATUS(status) == 0
