from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
import gc
import sys

import pytest
import retracesoftware.stream as stream
from retracesoftware.proxy import io as proxy_io

from retracesoftware.install.installation import Installation
from retracesoftware.install.patcher import patch
from retracesoftware.proxy.io import (
    _checkpoint_descriptor_marker,
    equal,
    recorder_context,
    replayer,
)
from retracesoftware.proxy.messagestream import (
    BindingStream,
    MessageStream,
    PeekableStream,
    SchedulerStream,
)
from retracesoftware.proxy.patchtype import patch_type
from retracesoftware.proxy.system import ProxyRef, RecordSystem, System
from retracesoftware.proxy.taggedtraceio import tagged_trace_writer
from retracesoftware.proxy.tape import Tape
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
def _recorder_context(mode, tape_writer):
    with recorder_context(
        writer=tagged_trace_writer(tape_writer.write),
        debug=mode.debug,
        stacktraces=mode.stacktraces,
    ) as system:
        _configure_system(system)
        yield system


def test_recorder_accepts_caller_constructed_trace_writer(monkeypatch):
    tape = []

    def write(*values):
        tape.extend(values)

    monkeypatch.setattr(proxy_io, "_load_retrace_probe", lambda *args, **kwargs: None)
    system = proxy_io.recorder(writer=tagged_trace_writer(write))
    tape.clear()
    try:
        system.lifecycle_hooks.on_start()
    finally:
        system.unpatch_types()

    assert tape == ["ON_START"]


def test_call_recorder_does_not_install_runtime_observation(monkeypatch):
    class FakeCallbacks:
        thread_switch = None

    class FakeRetrace:
        callbacks = FakeCallbacks()

        def thread_delta(self):
            return (0,)

    fake = FakeRetrace()
    tape = []

    def write(*values):
        tape.extend(values)

    monkeypatch.setattr(proxy_io, "_load_retrace_probe", lambda *args, **kwargs: fake)
    system = proxy_io.call_recorder(writer=tagged_trace_writer(write))
    tape.clear()
    try:
        system.lifecycle_hooks.on_start()
    finally:
        system.unpatch_types()

    assert tape == ["ON_START"]
    assert fake.callbacks.thread_switch is None


class CaptureTraceWriter:
    def __init__(self):
        self.calls = []

    def on_start(self):
        self.calls.append(("on_start",))

    def result(self, value):
        self.calls.append(("result", value))

    def error(self, error):
        self.calls.append(("error", error))

    def callback(self, fn, args, kwargs):
        self.calls.append(("callback", fn, args, kwargs))

    def callback_result(self, value):
        self.calls.append(("callback_result", value))

    def callback_error(self, error):
        self.calls.append(("callback_error", error))

    def checkpoint(self, value):
        self.calls.append(("checkpoint", value))

    def stacktrace(self, value):
        self.calls.append(("stacktrace", value))

    def thread_switch(self, cursor_delta, thread_id):
        self.calls.append(("thread_switch", cursor_delta, thread_id))

    def new_binding(self, handle):
        self.calls.append(("new_binding", handle))

    def binding_delete(self, handle):
        self.calls.append(("binding_delete", handle))

    def call_marker(self):
        self.calls.append(("call_marker",))

    def sync(self):
        self.calls.append(("sync",))


def test_call_recorder_passes_dynamic_external_proxy_to_writer():
    class DemoExternalWrapped(utils.ExternalWrapped):
        pass

    writer = CaptureTraceWriter()
    system = proxy_io.call_recorder(writer=writer)
    proxy = ProxyRef(DemoExternalWrapped)()
    writer.calls.clear()

    system.primary_hooks.on_result(proxy)

    assert len(writer.calls) == 1
    event, value = writer.calls[0]
    assert event == "result"
    assert value is proxy


def test_call_recorder_writes_bound_patched_object_as_binding():
    class Patched:
        pass

    writer = CaptureTraceWriter()
    system = proxy_io.call_recorder(writer=writer)
    patch_type(system, Patched)
    obj = Patched()
    writer.calls.clear()

    system.primary_hooks.on_result(obj)

    try:
        assert len(writer.calls) == 1
        event, value = writer.calls[0]
        assert event == "result"
        assert isinstance(value, stream.Binding)
    finally:
        system.unpatch_types()


def test_call_recorder_writes_bound_return_value_as_binding():
    class Bound:
        pass

    writer = CaptureTraceWriter()
    system = proxy_io.call_recorder(writer=writer)
    obj = Bound()
    system.bind(obj)
    writer.calls.clear()

    system.primary_hooks.on_result(obj)

    assert len(writer.calls) == 1
    event, value = writer.calls[0]
    assert event == "result"
    assert isinstance(value, stream.Binding)


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


def _result_index_after_on_start(raw, nth=0):
    seen = 0
    for index in range(raw.index("ON_START") + 1, len(raw)):
        if raw[index] != "RESULT":
            continue
        if seen == nth:
            return index
        seen += 1
    raise ValueError("RESULT not found after ON_START")


def _message_pipeline(items, *, initial_thread_id=0, current_thread_id=None, set_callback=None, handoff=None):
    class FlatTapeReader:
        def __init__(self, items):
            self._iter = iter(items)

        def read(self):
            return next(self._iter)

        def close(self):
            return None

    raw_messages = PeekableStream(MessageStream(FlatTapeReader(items).read))
    scheduler = SchedulerStream(
        raw_messages,
        set_callback=set_callback,
        handoff=handoff,
        initial_thread_id=initial_thread_id,
        current_thread_id=current_thread_id or (lambda: initial_thread_id),
        close=raw_messages.close,
    )
    messages = PeekableStream(scheduler)
    tape_reader = BindingStream(messages)
    return raw_messages, tape_reader, scheduler, tape_reader


def test_replayer_routes_thread_switch_and_records_previous_cursor():
    callbacks = []
    _, _, scheduler, messages = _message_pipeline(
        [
            "THREAD_SWITCH",
            1,
            (0, 1, 2),
            "RESULT",
            42,
        ],
        initial_thread_id=0,
        current_thread_id=lambda: 0,
        set_callback=callbacks.append,
    )

    message = messages.next()

    assert message.result == 42
    assert scheduler.current_thread_id() == 1
    assert len(callbacks) == 1
    assert callbacks[0].thread_id == 1
    assert scheduler.cursor(0) == (1, 2)


def test_replay_scheduler_rejects_thread_switch_without_thread_id():
    _, _, scheduler, messages = _message_pipeline([
        "THREAD_SWITCH",
        None,
        (0,),
        "RESULT",
        42,
    ])

    with pytest.raises(ValueError, match="requires thread_id"):
        messages.next()


def test_replay_scheduler_expands_thread_switch_delta():
    cursors = []
    scheduler_holder = []

    def on_switch(message):
        scheduler = scheduler_holder[0]
        cursors.append(scheduler.cursor(message.thread_id))

    _, _, scheduler, messages = _message_pipeline(
        [
            "THREAD_SWITCH",
            0,
            (0, 10, 20, 30),
            "RESULT",
            2,
            "THREAD_SWITCH",
            0,
            (2, 31),
            "RESULT",
            3,
        ],
        initial_thread_id=0,
        set_callback=on_switch,
    )
    scheduler_holder.append(scheduler)

    assert messages.next().result == 2
    assert cursors == [(10, 20, 30)]
    assert messages.next().result == 3
    assert cursors == [(10, 20, 30), (10, 20, 31)]


def test_replay_scheduler_runs_callback_after_thread_switch():
    seen = []
    _, _, scheduler, _messages = _message_pipeline(
        [
            "THREAD_SWITCH",
            0,
            (0, 3, 5),
            "CALLBACK",
            lambda: None,
            (),
            {},
            "CALLBACK_RESULT",
            None,
            "RESULT",
            42,
        ],
        initial_thread_id=0,
        set_callback=lambda _message: None,
    )
    scheduler.set_on_switch(lambda _message: seen.extend([scheduler.read(), scheduler.read()]))

    message = scheduler.read()

    assert [type(item).__name__ for item in seen] == [
        "CallbackMessage",
        "CallbackResultMessage",
    ]
    assert message.result == 42

def test_replay_scheduler_handoffs_on_thread_switch():
    callbacks = []
    current = [0]

    class FakeHandoff:
        def __init__(self):
            self.to_calls = []

        def to(self, thread_id):
            self.to_calls.append(thread_id)
            current[0] = thread_id

    handoff = FakeHandoff()
    _, _, scheduler, messages = _message_pipeline(
        [
            "THREAD_SWITCH",
            2,
            (0, 3),
            "RESULT",
            8,
        ],
        initial_thread_id=0,
        current_thread_id=lambda: current[0],
        set_callback=callbacks.append,
        handoff=handoff,
    )

    assert messages.next().result == 8
    assert scheduler.current_thread_id() == 2
    assert handoff.to_calls == [2]
    assert len(callbacks) == 1
    assert callbacks[0].thread_id == 2
    assert scheduler.cursor(0) == (3,)


def test_recorder_retrace_python_callback_writes_thread_switch(monkeypatch):
    class FakeCallbacks:
        def __init__(self):
            self.thread_switch = lambda previous_delta, next_thread_id: None

    class FakeRetrace:
        def __init__(self):
            self.callbacks = FakeCallbacks()

        def thread_delta(self):
            return (0, 21)

    fake = FakeRetrace()
    old_switch_callback = fake.callbacks.thread_switch
    tape = []

    def writer(*values):
        tape.extend(values)

    monkeypatch.setattr(proxy_io, "_load_retrace_probe", lambda: fake)
    system = proxy_io.recorder(writer=tagged_trace_writer(writer))
    tape.clear()

    system.lifecycle_hooks.on_start()
    try:
        assert fake.callbacks.thread_switch is not old_switch_callback

        fake.callbacks.thread_switch((3, 5, 8), 1)
        fake.callbacks.thread_switch((1, 13), 2)
    finally:
        system.lifecycle_hooks.on_end()

    assert tape == [
        "ON_START",
        "THREAD_SWITCH",
        1,
        (3, 5, 8),
        "THREAD_SWITCH",
        2,
        (1, 13),
    ]
    assert fake.callbacks.thread_switch is old_switch_callback


def test_recorder_gc_callback_writes_async_callback_envelope(monkeypatch):
    class FakeRetrace:
        def thread_delta(self):
            return (0, 10, 20, 30)

        def coordinates(self):
            return (10, 20, 30)

    fake = FakeRetrace()
    tape = []

    def writer(*values):
        tape.extend(values)

    monkeypatch.setattr(proxy_io, "_load_retrace_probe", lambda: fake)
    system = proxy_io.recorder(writer=tagged_trace_writer(writer))
    system.thread_id = lambda: "main-thread"
    tape.clear()

    was_enabled = gc.isenabled()
    gc.disable()
    system.lifecycle_hooks.on_start()
    try:
        gc.collect(0)
    finally:
        system.lifecycle_hooks.on_end()
        if was_enabled:
            gc.enable()

    switch_index = tape.index("THREAD_SWITCH")
    callback_index = tape.index("CALLBACK")
    result_index = tape.index("CALLBACK_RESULT")

    assert tape[switch_index + 1] == "main-thread"
    assert tape[switch_index + 2] == (0, 10, 20)
    assert tape[callback_index + 1] is gc.collect
    assert tape[callback_index + 2] == (0,)
    assert tape[callback_index + 3] == {}
    assert switch_index < callback_index < result_index


def test_recorder_signal_handler_writes_async_callback_envelope(monkeypatch):
    class FakeRetrace:
        def thread_delta(self):
            return (0, 40, 50, 60)

        def coordinates(self):
            return (40, 50, 60)

    fake = FakeRetrace()
    tape = []
    registered = {}
    handler_calls = []
    frame = object()

    def writer(*values):
        tape.extend(values)

    def signal_signal(signum, handler):
        previous = registered.get(signum, int(proxy_io.signal.SIG_DFL))
        registered[signum] = handler
        return previous

    def handler(signum, received_frame):
        handler_calls.append((signum, received_frame))
        return "handled"

    monkeypatch.setattr(proxy_io, "_load_retrace_probe", lambda: fake)
    monkeypatch.setattr(proxy_io._signal, "signal", signal_signal)
    system = proxy_io.recorder(writer=tagged_trace_writer(writer))
    system.thread_id = lambda: "main-thread"
    tape.clear()

    was_enabled = gc.isenabled()
    gc.disable()
    system.lifecycle_hooks.on_start()
    try:
        assert proxy_io.signal.signal(12, handler) == proxy_io.signal.SIG_DFL
        registered[12](12, frame)
    finally:
        system.lifecycle_hooks.on_end()
        if was_enabled:
            gc.enable()

    switch_index = tape.index("THREAD_SWITCH")
    callback_index = tape.index("CALLBACK")
    result_index = tape.index("CALLBACK_RESULT")

    assert handler_calls == [(12, frame)]
    assert tape[switch_index + 1] == "main-thread"
    assert tape[switch_index + 2] == (0, 40, 50)
    assert tape[callback_index + 1] is handler
    assert tape[callback_index + 2] == (12, None)
    assert tape[callback_index + 3] == {}
    assert tape[result_index + 1] == "handled"
    assert switch_index < callback_index < result_index


def test_replay_binding_state_consumes_trailing_binding_deletes():
    class FlatTapeReader:
        def __init__(self, items):
            self._iter = iter(items)

        def read(self):
            return next(self._iter)

    reader = BindingStream(
        PeekableStream(
            MessageStream(
                FlatTapeReader([
                    "BINDING_DELETE",
                    7,
                    "BINDING_DELETE",
                    8,
                    "RESULT",
                    "done",
                ]).read
            )
        )
    )
    reader.bind_handle(7, object())
    reader.bind_handle(8, object())

    reader.consume_pending_closes()

    assert reader._bindings == {}
    assert reader.read().result == "done"


def test_replay_binding_state_hydrates_proxy_ref_bindings():
    class DemoExternalWrapped(utils.ExternalWrapped):
        pass

    reader = BindingStream(PeekableStream(MessageStream(iter(()).__next__)))
    reader.bind_handle(7, ProxyRef(DemoExternalWrapped))

    resolved = reader.resolve({"value": [stream.Binding(7)]})

    assert isinstance(resolved["value"][0], DemoExternalWrapped)


def test_replayer_skips_nested_sync_call_marker_while_reading_external_result():
    """Regression for the flight-search HuggingFace replay failure.

    The real PidFile replay failed with:

        Unexpected message: CallMarkerMessage, was expecting a result, error, or call

    This builds the same protocol shape without depending on HuggingFace or a
    local model: an outer external call is replayed, but the trace contains a
    nested sync ``CALL`` frame before the outer ``RESULT``.  The higher-level
    protocol replay reader already knows how to skip nested sync-call frames;
    this test pins the same expectation for the proxy.io replay path used by
    extracted PidFiles.
    """

    tape = IOMemoryTape()
    writer = tape.writer()

    def external_download():
        return "cached-model-path"

    def flow(download):
        return download()

    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            download = record_system.patch_function(external_download)
            recorded = record_system.run(flow, download)

    assert recorded == "cached-model-path"

    raw = _raw_tape(tape)
    assert raw is not None
    outer_result_index = _result_index_after_on_start(raw)
    raw.insert(outer_result_index, "CALL")

    def raise_unexpected(message):
        raise AssertionError(
            f"Unexpected message: {message}, was expecting a result, error, or call"
        )

    reader = tape.reader()
    with _entered(reader) as reader:
        replay_system = replayer(
            next_object=reader.read,
            close=getattr(reader, "close", None),
            on_unexpected=raise_unexpected,
        )
        try:
            _configure_system(replay_system)
            download = replay_system.patch_function(external_download)
            replayed = replay_system.run(flow, download)
        finally:
            replay_system.unpatch_types()

    assert replayed == recorded


def test_replayer_raises_keyboard_interrupt_on_sync_while_reading_external_result():
    """Regression for Flask SIGINT PidFile replay shutdown."""

    tape = IOMemoryTape()
    writer = tape.writer()

    def external_wait():
        return "waited"

    def external_close():
        return "closed"

    def record_flow(wait, close):
        wait()
        return close()

    replay_system = None

    def replay_flow(wait, close):
        try:
            wait()
        except KeyboardInterrupt:
            replay_system.sync()
            return close()
        raise AssertionError("expected shutdown sync to interrupt the wait")

    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            wait = record_system.patch_function(external_wait)
            close = record_system.patch_function(external_close)
            recorded = record_system.run(record_flow, wait, close)

    assert recorded == "closed"

    raw = _raw_tape(tape)
    assert raw is not None
    wait_result_index = _result_index_after_on_start(raw)
    del raw[wait_result_index:wait_result_index + 2]
    raw.insert(wait_result_index, "SYNC")

    def raise_unexpected(message):
        raise AssertionError(
            f"Unexpected message: {message}, was expecting a result, error, or call"
        )

    reader = tape.reader()
    with _entered(reader) as reader:
        replay_system = replayer(
            next_object=reader.read,
            close=getattr(reader, "close", None),
            on_unexpected=raise_unexpected,
        )
        try:
            _configure_system(replay_system)
            wait = replay_system.patch_function(external_wait)
            close = replay_system.patch_function(external_close)
            replayed = replay_system.run(replay_flow, wait, close)
        finally:
            replay_system.unpatch_types()

    assert replayed == recorded


def test_replayer_raises_keyboard_interrupt_on_terminal_sync_at_eof():
    tape = IOMemoryTape()
    writer = tape.writer()

    def external_wait():
        return "last-result"

    def record_flow(wait):
        return wait()

    def replay_flow(wait):
        try:
            wait()
        except KeyboardInterrupt:
            return "interrupted"
        raise AssertionError("expected shutdown sync to interrupt the wait")

    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            wait = record_system.patch_function(external_wait)
            recorded = record_system.run(record_flow, wait)

    assert recorded == "last-result"

    raw = _raw_tape(tape)
    assert raw is not None
    outer_result_index = _result_index_after_on_start(raw)
    del raw[outer_result_index:outer_result_index + 2]
    raw.insert(outer_result_index, "SYNC")

    reader = tape.reader()
    with _entered(reader) as reader:
        replay_system = replayer(
            next_object=reader.read,
            close=getattr(reader, "close", None),
            on_unexpected=lambda message: (_ for _ in ()).throw(
                AssertionError(f"unexpected: {message!r}")
            ),
        )
        try:
            _configure_system(replay_system)
            wait = replay_system.patch_function(external_wait)
            replayed = replay_system.run(replay_flow, wait)
        finally:
            replay_system.unpatch_types()

    assert replayed == "interrupted"


def test_checkpoint_equal_matches_descriptor_marker_to_proxy_shell():
    import ssl

    descriptor = super(ssl.SSLContext, ssl.SSLContext).minimum_version
    marker = _checkpoint_descriptor_marker(descriptor)

    system = RecordSystem()
    try:
        descriptor_proxy = system.descriptor_proxytype(type(descriptor))(descriptor)
        assert equal(marker, descriptor_proxy)

        ProxyDescriptor = type(
            "getset_descriptor",
            (utils.ExternalWrapped,),
            {"__module__": "builtins"},
        )
        proxy_shell = utils.create_wrapped(ProxyDescriptor, None)
        assert equal(marker, proxy_shell)
    finally:
        system.unpatch_types()


def test_checkpoint_equal_matches_memoryview_and_bytes_by_content():
    assert equal(memoryview(b"abc"), b"abc")
    assert equal(b"abc", memoryview(b"abc"))
    assert not equal(memoryview(b"abc"), b"abd")


def test_raw_tape_source_consumes_binding_delete_before_replay_scheduler():
    _, tape_reader, _scheduler, messages = _message_pipeline(
        [
            "BINDING_DELETE",
            7,
            "RESULT",
            42,
        ],
        initial_thread_id=0,
        current_thread_id=lambda: 0,
    )
    tape_reader.bind_handle(7, object())

    assert messages.next().result == 42
    assert tape_reader._bindings == {}


def test_replayer_skips_standalone_callback_result_before_next_call():
    tape = IOMemoryTape()
    writer = tape.writer()
    live_calls = []
    callback_calls = []

    def callback():
        callback_calls.append("callback")
        return "callback-result"

    def external():
        live_calls.append("external")
        return 42

    def flow(call, emit_callback):
        if emit_callback is not None:
            emit_callback()
        return call()

    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            recorded_external = record_system.patch_function(external)

            def emit_callback_envelope():
                record_system.write_callback(callback)
                record_system.write_callback_result("callback-result")

            recorded = record_system.run(flow, recorded_external, emit_callback_envelope)

    assert recorded == 42
    assert live_calls == ["external"]
    assert callback_calls == []

    raw = _raw_tape(tape)
    assert raw is not None
    assert "CALLBACK" in raw
    assert "CALLBACK_RESULT" in raw

    def on_desync(record, replay):
        raise AssertionError(f"desync: {record!r} vs {replay!r}")

    def on_unexpected(message):
        raise AssertionError(f"unexpected: {message!r}")

    reader = tape.reader()
    with _entered(reader) as reader:
        replay_system = replayer(
            next_object=reader.read,
            close=getattr(reader, "close", None),
            on_desync=on_desync,
            on_unexpected=on_unexpected,
        )
        try:
            _configure_system(replay_system)
            replayed_external = replay_system.patch_function(external)
            replayed = replay_system.run(flow, replayed_external, None)
        finally:
            replay_system.unpatch_types()

    assert replayed == 42
    assert live_calls == ["external"]
    assert callback_calls == ["callback"]


def test_replayer_skips_stub_callback_before_async_new_patched_bind():
    tape = IOMemoryTape()

    class External:
        pass

    def flow(system, obj):
        system.async_new_patched(obj)
        assert system.is_bound(obj)
        return "ok"

    writer = tape.writer()
    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            patch_type(record_system, External)
            recorded_obj = record_system.apply_with(None, External)()
            assert record_system.run(flow, record_system, recorded_obj) == "ok"

    raw = _raw_tape(tape)
    assert raw is not None
    assert "CALLBACK" in raw
    assert "NEW_BINDING" in raw

    reader = tape.reader()
    with _entered(reader) as reader:
        replay_system = replayer(
            next_object=reader.read,
            close=getattr(reader, "close", None),
            on_desync=lambda record, replay: (_ for _ in ()).throw(
                AssertionError(f"desync: {record!r} vs {replay!r}")
            ),
            on_unexpected=lambda message: (_ for _ in ()).throw(
                AssertionError(f"unexpected: {message!r}")
            ),
        )
        try:
            _configure_system(replay_system)
            patch_type(replay_system, External)
            replay_obj = replay_system.apply_with(None, External)()
            assert replay_system.run(flow, replay_system, replay_obj) == "ok"
        finally:
            replay_system.unpatch_types()


def test_replayer_runs_callback_before_internal_patched_alloc_bind():
    tape = IOMemoryTape()
    callback_calls = []

    class External:
        pass

    def callback():
        callback_calls.append("callback")
        return "callback-result"

    def flow(cls, emit_callback):
        if emit_callback is not None:
            emit_callback()
        obj = cls()
        return type(obj).__name__

    writer = tape.writer()
    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            patch_type(record_system, External)

            def emit_callback_envelope():
                record_system.write_callback(callback)
                record_system.write_callback_result("callback-result")

            recorded = record_system.run(flow, External, emit_callback_envelope)

    assert recorded == "External"
    assert callback_calls == []

    raw = _raw_tape(tape)
    assert raw is not None
    assert "CALLBACK" in raw
    callback_index = raw.index("CALLBACK")
    assert "NEW_BINDING" in raw[callback_index:]

    reader = tape.reader()
    with _entered(reader) as reader:
        replay_system = replayer(
            next_object=reader.read,
            close=getattr(reader, "close", None),
            on_desync=lambda record, replay: (_ for _ in ()).throw(
                AssertionError(f"desync: {record!r} vs {replay!r}")
            ),
            on_unexpected=lambda message: (_ for _ in ()).throw(
                AssertionError(f"unexpected: {message!r}")
            ),
        )
        try:
            _configure_system(replay_system)
            patch_type(replay_system, External)
            replayed = replay_system.run(flow, External, None)
        finally:
            replay_system.unpatch_types()

    assert replayed == recorded
    assert callback_calls == ["callback"]


def test_replayer_skips_result_before_internal_patched_alloc_bind():
    tape = IOMemoryTape()

    class External:
        pass

    def flow(cls, emit_external_envelope):
        if emit_external_envelope is not None:
            emit_external_envelope()
        obj = cls()
        return type(obj).__name__

    writer = tape.writer()
    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            patch_type(record_system, External)

            def emit_external_envelope():
                record_system.primary_hooks.on_result("external-result")

            recorded = record_system.run(flow, External, emit_external_envelope)

    assert recorded == "External"
    raw = _raw_tape(tape)
    assert raw is not None
    result_index = raw.index("RESULT")
    binding_index = raw.index("NEW_BINDING", result_index)
    assert result_index < binding_index

    reader = tape.reader()
    with _entered(reader) as reader:
        replay_system = replayer(
            next_object=reader.read,
            close=getattr(reader, "close", None),
            on_desync=lambda record, replay: (_ for _ in ()).throw(
                AssertionError(f"desync: {record!r} vs {replay!r}")
            ),
            on_unexpected=lambda message: (_ for _ in ()).throw(
                AssertionError(f"unexpected: {message!r}")
            ),
        )
        try:
            _configure_system(replay_system)
            patch_type(replay_system, External)
            replayed = replay_system.run(flow, External, None)
        finally:
            replay_system.unpatch_types()

    assert replayed == recorded


def test_ext_proxy_result_live_runs_factory_and_wraps_returned_object():
    tape = IOMemoryTape()
    calls = []
    phase_values = []
    proxy_values = []
    active_system = []

    class ExternalResource:
        pass

    def factory(label):
        phase_values.append(active_system[-1].location)
        calls.append(label)
        return ExternalResource()

    def flow(make_resource, label):
        resource = make_resource(label)
        proxy_values.append(
            (isinstance(resource, utils.ExternalWrapped), active_system[-1].is_bound(resource))
        )
        return type(resource).__name__

    namespace = {"__name__": "local_factory", "factory": factory}

    writer = tape.writer()
    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            undo = patch(
                namespace,
                {"ext_proxy_result": ["factory"]},
                Installation(record_system),
            )
            try:
                active_system.append(record_system)
                recorded = record_system.run(flow, namespace["factory"], "record")
            finally:
                active_system.pop()
                undo()

    raw = _raw_tape(tape)
    assert raw is not None
    assert "RESULT" not in raw
    assert "NEW_BINDING" in raw
    assert calls == ["record"]

    namespace["factory"] = factory
    reader = tape.reader()
    with _entered(reader) as reader:
        replay_system = replayer(
            next_object=reader.read,
            close=getattr(reader, "close", None),
            on_desync=lambda record, replay: (_ for _ in ()).throw(
                AssertionError(f"desync: {record!r} vs {replay!r}")
            ),
            on_unexpected=lambda message: (_ for _ in ()).throw(
                AssertionError(f"unexpected: {message!r}")
            ),
        )
        try:
            _configure_system(replay_system)
            undo = patch(
                namespace,
                {"ext_proxy_result": ["factory"]},
                Installation(replay_system),
            )
            try:
                active_system.append(replay_system)
                replayed = replay_system.run(flow, namespace["factory"], "replay")
            finally:
                active_system.pop()
                undo()
        finally:
            replay_system.unpatch_types()

    assert replayed == recorded
    assert calls == ["record", "replay"]
    assert phase_values == ["internal", "internal"]
    assert proxy_values == [(True, False), (True, False)]


def test_system_io_records_callback_for_new_ext_proxy_type_with_memory_tape():
    tape = IOMemoryTape()
    writer = tape.writer()

    with _entered(writer) as writer:
        with _recorder_context(IOMode("plain"), writer) as record_system:
            recorded_make = record_system.patch_function(make_fresh_external_result)
            record_system.run(recorded_make)

    raw = _raw_tape(tape)

    assert raw is not None
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
            patch_type(record_system, Base)

            def trigger(cls, value):
                return cls().trigger(value)

            recorded = record_system.run(trigger, Sub, 5)

    assert recorded == 11
    assert live_calls == [("external", 5), ("callback", 5)]

    raw = _raw_tape(tape)
    if raw is not None:
        if mode.debug:
            assert "CHECKPOINT" in raw
        assert "RESULT" in raw

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(mode, reader) as replay_system:
            patch_type(replay_system, Base)

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
            patch_type(record_system, Base)
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
            patch_type(replay_system, Base)
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
            patch_type(record_system, Base)
            current_clock = record_system.patch_function(clock)
            recorded = record_system.run(lambda cls, value: cls().trigger(value), Sub, 5)

    assert recorded == 106
    assert live_calls == [("external", 5), ("callback", 5), "clock"]

    reader = tape.reader()
    with _entered(reader) as reader:
        with _replayer_context(IOMode("plain"), reader) as replay_system:
            patch_type(replay_system, Base)
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
            patch_type(record_system, External)

            def run_callback(external_cls, callback_cls, calls):
                return external_cls().run(callback_cls(calls))

            recorded = record_system.run(run_callback, External, Callback, record_calls)

    assert recorded == 11
    assert record_calls == ["called"]

    reader = tape.reader()
    replay_calls = []
    with _entered(reader) as reader:
        with _replayer_context(IOMode("plain"), reader) as replay_system:
            patch_type(replay_system, External)

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
    from retracesoftware.proxy.system import RecordSystem

    pid = os.fork()
    if pid == 0:
        system = RecordSystem()
        _configure_system(system)
        descriptor_type = type(_socket.socket.__dict__["family"])
        system.ext_proxytype(descriptor_type)
        os._exit(0)

    _, status = os.waitpid(pid, 0)

    assert os.WIFEXITED(status)
    assert os.WEXITSTATUS(status) == 0


def test_generated_ext_proxytype_getattr_forwards_declared_descriptor():
    import _io
    from retracesoftware.proxy.system import RecordSystem

    system = RecordSystem()
    _configure_system(system)
    proxy_type = system.ext_proxytype(_io.BufferedReader)

    raw = _io.BufferedReader(_io.BytesIO(b"hello"))
    wrapped = utils.create_wrapped(proxy_type, raw)
    try:
        assert "__getattr__" in proxy_type.__dict__
        assert wrapped.closed is False
    finally:
        raw.close()


def test_generated_ext_proxytype_setattr_forwards_dynamic_attrs():
    from retracesoftware.proxy.system import RecordSystem

    system = RecordSystem()
    _configure_system(system)
    proxy_type = system.ext_proxytype(FreshExternalResult)

    raw = FreshExternalResult()
    wrapped = utils.create_wrapped(proxy_type, raw)

    assert "__setattr__" in proxy_type.__dict__
    wrapped.value = 42

    assert raw.value == 42
