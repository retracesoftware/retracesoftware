import traceback
import weakref
from argparse import Namespace
from contextlib import contextmanager
from typing import NamedTuple

from retracesoftware import functional
from retracesoftware.protocol.messages import HandleMessage, ThreadSwitchMessage
from retracesoftware.protocol.normalize import normalize as normalize_checkpoint_value
from retracesoftware.protocol.record import CALL
from retracesoftware.protocol.replay import ReplayReader, StacktraceFactory
from retracesoftware.proxy.tape import TapeReader, TapeWriter
from retracesoftware.stream import (
    Binder,
    Binding,
    ObjectReader,
    ThreadSwitch,
    _BIND_OPEN_TAG,
)
import retracesoftware.utils as utils


class _BindOpenMarker:
    __slots__ = ("index",)

    def __init__(self, index):
        self.index = index

    def __repr__(self):
        return f"BindOpen({self.index})"


class _BindCloseMarker:
    __slots__ = ("index",)

    def __init__(self, index):
        self.index = index

    def __repr__(self):
        return f"BindClose({self.index})"


class _BindingState:
    __slots__ = (
        "_binder",
        "_indices",
        "_fallback_bindings",
        "_next_index",
        "_tape_append",
    )

    def __init__(self, tape_append):
        self._binder = Binder(on_delete=self._on_delete)
        self._indices = {}
        self._fallback_bindings = {}
        self._next_index = 0
        self._tape_append = tape_append

    def _index_for_key(self, key):
        index = self._indices.get(key)
        if index is None:
            index = self._next_index
            self._next_index += 1
            self._indices[key] = index
        return index

    def _on_delete(self, binding):
        handle = binding.handle if hasattr(binding, "handle") else binding
        index = self._indices.pop(("binding", handle), None)
        if index is not None:
            self._tape_append(_BindCloseMarker(index))

    def _on_collect(self, obj_id):
        key = ("fallback", obj_id)
        index = self._indices.pop(key, None)
        self._fallback_bindings.pop(obj_id, None)
        if index is not None:
            self._tape_append(_BindCloseMarker(index))

    def _bind_fallback(self, obj):
        obj_id = id(obj)
        index = self._fallback_bindings.get(obj_id)
        if index is None:
            key = ("fallback", obj_id)
            index = self._index_for_key(key)
            self._fallback_bindings[obj_id] = index
            try:
                weakref.finalize(obj, self._on_collect, obj_id)
            except TypeError:
                pass
        return _BindOpenMarker(index)

    def bind(self, obj):
        try:
            binding = self._binder.bind(obj)
        except TypeError:
            return self._bind_fallback(obj)
        return _BindOpenMarker(self._index_for_key(("binding", binding.handle)))

    def __call__(self, obj, fallback=None):
        binding = self._binder.lookup(obj)
        if binding is not None:
            index = self._indices.get(("binding", binding.handle))
            if index is not None:
                return Binding(index)
        fallback_index = self._fallback_bindings.get(id(obj))
        if fallback_index is not None:
            return Binding(fallback_index)
        return fallback(obj) if fallback else obj


class _MemoryTapeWriter:
    """Low-level writer surface used by ``proxy.io.IO`` tests."""

    __slots__ = ("_tape_append", "_bindings", "_write_one")

    def __init__(self, tape_append, serializer=None):
        self._tape_append = tape_append
        self._bindings = _BindingState(tape_append)
        self._write_one = functional.sequence(
            functional.walker(lambda obj: self._bindings(obj, serializer)),
            tape_append,
        )

    def write(self, *values, **kwargs):
        if kwargs:
            if len(values) < 2:
                raise TypeError("write(..., **kwargs) requires at least a tag and callable")
            prefix = values[:2]
            arguments = values[2:]
            for value in prefix:
                self._write_one(value)
            self._write_one(arguments)
            self._write_one(kwargs)
            return None

        for value in values:
            self._write_one(value)
        return None

    def bind(self, obj):
        self._tape_append(self._bindings.bind(obj))
        return None

    def monitor_event(self, value):
        self.write("MONITOR", value)


class _MemoryTapeReader:
    """Low-level reader surface used by ``proxy.io.IO`` tests."""

    __slots__ = ("_buffer", "_next_from_tape", "_bindings")

    def __init__(self, next_from_tape):
        self._buffer = []
        self._next_from_tape = next_from_tape
        self._bindings = {}

    def _next_raw(self):
        if self._buffer:
            return self._buffer.pop(0)
        return self._next_from_tape()

    def _next_visible(self):
        value = self._next_raw()
        while isinstance(value, _BindCloseMarker):
            self._bindings.pop(value.index, None)
            value = self._next_raw()
        return value

    def _resolve(self, value, bindings=None):
        if bindings is None:
            bindings = self._bindings
        if isinstance(value, Binding):
            return bindings[value.handle]
        elif isinstance(value, _BindOpenMarker):
            raise RuntimeError(f"unexpected bind marker, got {value!r}")
        else:
            return value

    def read(self):
        return functional.walker(self._resolve)(self._next_visible())

    def bind(self, obj):
        marker = self._next_visible()
        if not isinstance(marker, _BindOpenMarker):
            raise RuntimeError(f"expected bind marker, got {marker!r}")
        self._bindings[marker.index] = obj
        return None

    def peek(self):
        shadow_bindings = dict(self._bindings)
        index = 0

        while True:
            while index >= len(self._buffer):
                self._buffer.append(self._next_from_tape())

            value = self._buffer[index]
            index += 1

            if isinstance(value, _BindCloseMarker):
                shadow_bindings.pop(value.index, None)
                continue

            if isinstance(value, _BindOpenMarker):
                raise RuntimeError(f"unexpected bind marker, got {value!r}")

            return functional.walker(lambda obj: self._resolve(obj, shadow_bindings))(value)

    def monitor_checkpoint(self, value):
        marker = self._next_visible()
        if marker != "MONITOR":
            raise RuntimeError(f"expected 'MONITOR', got {marker!r}")
        recorded = self.read()
        if recorded != value:
            raise RuntimeError(
                f"monitor divergence: recorded {recorded!r}, replayed {value!r}"
            )


class TapeInvariantError(BaseException):
    pass


def _is_harness_frame(frame):
    filename = frame.filename
    return (
        filename == "<frozen runpy>"
        or "/site-packages/pytest/" in filename
        or "/site-packages/_pytest/" in filename
        or "/site-packages/pluggy/" in filename
    )


def _trim_stacktrace_frames(frames):
    trimmed = [frame for frame in frames if not _is_harness_frame(frame)]
    return trimmed or frames


def _stacktrace_message():
    frames = _trim_stacktrace_frames(traceback.extract_stack()[:-2])
    return "".join(traceback.format_list(frames)).rstrip()


def default_serializer(obj):
    import time

    if obj is not None and not isinstance(obj, (int, bytes, str, bool, float, time.struct_time)):
        stacktrace = _stacktrace_message()
        error = TapeInvariantError(
            f"Unexpected object type for serialization: {type(obj)}\n"
            f"Serialization stack:\n{stacktrace}"
        )
        error.stacktrace = stacktrace
        error.value_type = type(obj)
        error.value_repr = repr(obj)
        raise error

    return obj


class MemoryTape:
    """Low-level in-memory tape with ``write/read`` + ``bind`` surfaces."""

    __slots__ = ("tape", "serializer")

    def __init__(self, tape=None, serializer=None):
        self.tape = [] if tape is None else list(tape)
        self.serializer = serializer if serializer else default_serializer

    def writer(self) -> TapeWriter:
        return _MemoryTapeWriter(self.tape.append, self.serializer)

    def reader(self) -> TapeReader:
        return _MemoryTapeReader(iter(self.tape).__next__)


class IOMemoryTape:
    """Raw in-memory tape for ``proxy.io`` record/replay paths."""

    __slots__ = ("tape",)

    def __init__(self, tape=None):
        self.tape = [] if tape is None else list(tape)

    def writer(self) -> TapeWriter:
        return _IOThreadAwareTapeWriter(self.tape)

    def reader(self):
        return _IOThreadAwareTapeReader(self.tape)


class _ProtocolBindingState:
    __slots__ = ("_bindings", "_next_index")

    def __init__(self):
        self._bindings = {}
        self._next_index = 0

    def bind(self, obj):
        obj_id = id(obj)
        index = self._bindings.get(obj_id)
        if index is None:
            index = self._next_index
            self._next_index += 1
        self._bindings[obj_id] = index
        return index

    def lookup(self, obj):
        return self._bindings.get(id(obj))


class _ProtocolTapeSource:
    """Flat in-memory tape source for the shared stream reader stack."""

    __slots__ = ("_tape", "_pos")

    def __init__(self, tape):
        self._tape = tape
        self._pos = 0

    def __call__(self):
        if self._pos >= len(self._tape):
            raise StopIteration
        item = self._tape[self._pos]
        self._pos += 1
        if isinstance(item, ThreadSwitchMessage):
            return ThreadSwitch(item.thread_id)
        return item

    def close(self):
        return None


class MemoryWriter:
    """Write protocol messages to an in-memory tape."""

    __slots__ = (
        "tape",
        "_stackfactory",
        "_thread",
        "_last_thread",
        "type_serializer",
        "_binding_state",
        "_interned",
        "_checkpoint_stackfactory",
        "_stacktrace_message_factory",
    )

    def __init__(self, stackfactory=None, thread=None):
        self.tape = []
        self._stackfactory = stackfactory
        self._thread = thread
        self._last_thread = thread() if thread else None
        self.type_serializer = {}
        self._binding_state = _ProtocolBindingState()
        self._interned = {}
        exclude = getattr(stackfactory, "exclude", None) if stackfactory is not None else None
        self._checkpoint_stackfactory = utils.StackFactory(exclude=exclude)
        self._stacktrace_message_factory = StacktraceFactory()

    def _maybe_switch(self):
        if self._thread is not None:
            tid = self._thread()
            if tid != self._last_thread:
                self._last_thread = tid
                self.tape.append(ThreadSwitch(tid))

    def _current_thread_id(self):
        if self._thread is not None:
            return self._thread()
        return self._last_thread

    def _encode_value(self, value):
        serializer = self.type_serializer.get(type(value))
        if serializer is not None:
            return serializer(value)

        binding_index = self._binding_state.lookup(value)
        if binding_index is not None:
            return Binding(binding_index)

        if isinstance(value, list):
            return [self._encode_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._encode_value(item) for item in value)
        if isinstance(value, dict):
            return {
                self._encode_value(key): self._encode_value(item)
                for key, item in value.items()
            }
        return value

    def sync(self):
        self._maybe_switch()
        self.tape.append("SYNC")

    def write_call(self, *args, **kwargs):
        self._maybe_switch()
        self.tape.append(CALL)

    def write_result(self, value):
        self._maybe_switch()
        self.tape.append("RESULT")
        self.tape.append(self._encode_value(value))

    def write_error(self, exc_type, exc_value, exc_tb):
        self._maybe_switch()
        self.tape.append("ERROR")
        self.tape.append(exc_value)

    def async_call(self, fn, *args, **kwargs):
        self._maybe_switch()
        self.tape.append("ASYNC_CALL")
        self.tape.append(self._encode_value(fn))
        self.tape.append(self._encode_value(args))
        self.tape.append(self._encode_value(kwargs))

    def checkpoint(self, value):
        self._maybe_switch()
        self.tape.append(
            self._stacktrace_message_factory.materialize(
                *self._checkpoint_stackfactory.delta(),
                self._current_thread_id(),
            )
        )
        self.tape.append("CHECKPOINT")
        self.tape.append(normalize_checkpoint_value(value))

    def monitor_event(self, value):
        self._maybe_switch()
        self.tape.append("MONITOR")
        self.tape.append(value)

    def stacktrace(self):
        if self._stackfactory is not None:
            self.tape.append(
                self._stacktrace_message_factory.materialize(
                    *self._stackfactory.delta(),
                    self._current_thread_id(),
                )
            )

    def handle(self, name):
        tape = self.tape

        def handle_writer(value):
            tape.append(
                HandleMessage(
                    name,
                    value,
                    thread_id=self._current_thread_id(),
                )
            )

        return handle_writer

    def bind(self, *a, **kw):
        if not a:
            return None
        index = self._binding_state.bind(a[0])
        self.tape.append((_BIND_OPEN_TAG, index))
        return None

    def intern(self, obj):
        self._interned[id(obj)] = obj
        return None

    def async_new_patched(self, obj):
        self._maybe_switch()
        self.tape.append("ASYNC_NEW_PATCHED")
        self.tape.append(self._encode_value(obj))

    def reader(self, stacktrace_factory=None):
        return MemoryReader(self.tape, stacktrace_factory=stacktrace_factory)


class MemoryReader:
    """Read a protocol tape written by ``MemoryWriter``."""

    def __init__(self, tape, timeout=None, monitor_enabled=False, stacktrace_factory=None):
        import retracesoftware.utils as utils

        self._type_deserializer = {}
        self._monitor_enabled = monitor_enabled
        self.mark_retraced = utils.noop
        self._tape = tape
        self._tape_len = len(tape)
        self.stub_factory = utils.create_stub_object
        self._source = _ProtocolTapeSource(tape)
        self._native_reader = ObjectReader(thread_id=None, source=self._source)
        self._stream = ReplayReader(
            self._native_reader,
            bind=self.bind,
            mark_retraced=self.mark_retraced,
            stub_factory=self.stub_factory,
            monitor_enabled=monitor_enabled,
            stacktrace_factory=stacktrace_factory,
        )
        self._stream.type_deserializer = self._type_deserializer

    def _get_stream(self):
        return self._stream

    def sync(self):
        self._get_stream().sync()

    def write_call(self, *args, **kwargs):
        self._get_stream().write_call(*args, **kwargs)

    def on_call(self, *args, **kwargs):
        return self.write_call(*args, **kwargs)

    def read_result(self):
        return self._get_stream().read_result()

    def bind(self, *a, **kw):
        if not a:
            return None
        return self._native_reader.bind(a[0])

    def checkpoint(self, value):
        self._get_stream().checkpoint(value)

    def monitor_checkpoint(self, value):
        self._get_stream().monitor_checkpoint(value)

    @property
    def type_deserializer(self):
        return self._type_deserializer

    @type_deserializer.setter
    def type_deserializer(self, value):
        self._type_deserializer = value
        self._stream.type_deserializer = value

    @property
    def remaining(self):
        return max(0, self._tape_len - self._source._pos)


class RecordReplayResult(NamedTuple):
    recorded: object
    replayed: object
    remaining: list
    tape: list


def _default_install_options():
    return Namespace(
        monitor=0,
        retrace_file_patterns=None,
        verbose=False,
        trace_shutdown=False,
    )


def _drain_tape_reader(reader):
    source = getattr(reader, "_source", None)
    tape = getattr(reader, "_tape", None)
    if source is not None and tape is not None and hasattr(source, "_pos"):
        return list(tape[source._pos :])

    if hasattr(reader, "read"):
        items = []
        while True:
            try:
                items.append(reader.read())
            except StopIteration:
                return items

    items = []
    return items


class _IOThreadAwareTapeWriter:
    __slots__ = ("_writer", "tape")

    def __init__(self, tape, *, thread=None):
        self._writer = MemoryWriter(thread=thread)
        self._writer.tape = tape
        self.tape = self._writer.tape

    def write(self, *values):
        self._writer._maybe_switch()
        for value in values:
            self._writer.tape.append(self._writer._encode_value(value))
        return None

    def bind(self, obj):
        self._writer._maybe_switch()
        return self._writer.bind(obj)

    def monitor_event(self, value):
        return self.write("MONITOR", value)


class _IOThreadAwareTapeReader:
    __slots__ = ("_tape", "_source")

    def __init__(self, tape):
        self._tape = tape
        self._source = _ProtocolTapeSource(tape)

    def read(self):
        return self._source()

    def close(self):
        return None

    def monitor_checkpoint(self, value):
        marker = self.read()
        if marker != "MONITOR":
            raise RuntimeError(f"expected 'MONITOR', got {marker!r}")
        recorded = self.read()
        if recorded != value:
            raise RuntimeError(
                f"monitor divergence: recorded {recorded!r}, replayed {value!r}"
            )


@contextmanager
def _thread_id_context(thread_ids):
    import _thread
    import threading

    original_start_new_thread = _thread.start_new_thread
    original_threading_start_new_thread = threading._start_new_thread
    wrapped_start_new_thread = thread_ids.wrap_start_new_thread(original_start_new_thread)

    _thread.start_new_thread = wrapped_start_new_thread
    threading._start_new_thread = wrapped_start_new_thread
    try:
        yield
    finally:
        _thread.start_new_thread = original_start_new_thread
        threading._start_new_thread = original_threading_start_new_thread


def record_then_replay(
    function,
    *,
    args=(),
    kwargs=None,
    tape=None,
    configure_system=None,
    after_record=None,
    after_replay=None,
    debug=False,
    stacktraces=False,
    options=None,
    inject_system=False,
):
    from retracesoftware.install import ReplayDivergence, install_retrace
    from retracesoftware.proxy.io import recorder, replayer
    from retracesoftware.threadid import ThreadId

    kwargs = {} if kwargs is None else dict(kwargs)
    options = _default_install_options() if options is None else options
    if tape is None:
        tape_storage = []
    elif hasattr(tape, "tape"):
        tape_storage = tape.tape
    else:
        raise TypeError(
            "record_then_replay expects tape to be None or expose a .tape list"
        )

    record_thread_ids = ThreadId()
    writer = _IOThreadAwareTapeWriter(
        tape_storage,
        thread=record_thread_ids.id.get,
    )
    record_system = recorder(
        writer=writer.write,
        debug=debug,
        stacktraces=stacktraces,
    )
    if configure_system is not None:
        configure_system(record_system)
    if inject_system:
        def record_function():
            return function(record_system, *args, **kwargs)
    else:
        def record_function():
            return function(*args, **kwargs)
    with _thread_id_context(record_thread_ids):
        uninstall_record = install_retrace(
            system=record_system,
            monitor_level=getattr(options, "monitor", 0),
            retrace_file_patterns=getattr(options, "retrace_file_patterns", None),
            verbose=options.verbose,
            retrace_shutdown=options.trace_shutdown,
        )
        try:
            recorded = record_system.run(record_function)
        finally:
            uninstall_record()
    if after_record is not None:
        after_record()

    replay_thread_ids = ThreadId()
    tape_reader = _IOThreadAwareTapeReader(tape_storage)

    def on_unexpected(key):
        raise ReplayDivergence(
            f"unexpected message during replay: {key!r}",
            tape=list(tape_storage),
        )

    def on_desync(record, replay):
        raise ReplayDivergence(
            f"Checkpoint difference: {record!r} was expecting {replay!r}",
            tape=list(tape_storage),
        )

    replay_system = replayer(
        next_object=tape_reader.read,
        close=tape_reader.close,
        on_unexpected=on_unexpected,
        on_desync=on_desync,
        debug=debug,
        stacktraces=stacktraces,
    )
    if configure_system is not None:
        configure_system(replay_system)
    if inject_system:
        def replay_function():
            return function(replay_system, *args, **kwargs)
    else:
        def replay_function():
            return function(*args, **kwargs)
    with _thread_id_context(replay_thread_ids):
        uninstall_replay = install_retrace(
            system=replay_system,
            monitor_level=getattr(options, "monitor", 0),
            retrace_file_patterns=getattr(options, "retrace_file_patterns", None),
            verbose=options.verbose,
            retrace_shutdown=options.trace_shutdown,
        )
        try:
            replayed = replay_system.run(replay_function)
        finally:
            uninstall_replay()
    if after_replay is not None:
        after_replay()

    return RecordReplayResult(
        recorded=recorded,
        replayed=replayed,
        remaining=_drain_tape_reader(tape_reader),
        tape=list(tape_storage),
    )


__all__ = [
    "MemoryReader",
    "MemoryTape",
    "MemoryWriter",
    "RecordReplayResult",
    "_BindCloseMarker",
    "record_then_replay",
]
