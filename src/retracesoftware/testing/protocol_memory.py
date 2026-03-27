"""In-memory protocol backend used by tests and pytest helpers."""

from retracesoftware.protocol import HandleMessage
from retracesoftware.protocol.messages import ThreadSwitchMessage
from retracesoftware.protocol.replay import ReplayReader
from retracesoftware.stream import BindingCreate, BindingLookup, ObjectReader, ThreadSwitch


class _BindingState:
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


class _MemoryTapeSource:
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
    )

    def __init__(self, stackfactory=None, thread=None):
        self.tape = []
        self._stackfactory = stackfactory
        self._thread = thread
        self._last_thread = thread() if thread else None
        self.type_serializer = {}
        self._binding_state = _BindingState()
        self._interned = {}

    def _maybe_switch(self):
        if self._thread is not None:
            tid = self._thread()
            if tid != self._last_thread:
                self._last_thread = tid
                self.tape.append(ThreadSwitch(tid))

    def _encode_value(self, value):
        serializer = self.type_serializer.get(type(value))
        if serializer is not None:
            return serializer(value)

        binding_index = self._binding_state.lookup(value)
        if binding_index is not None:
            return BindingLookup(binding_index)

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
        self.tape.append("CHECKPOINT")
        self.tape.append(value)

    def monitor_event(self, value):
        self._maybe_switch()
        self.tape.append("MONITOR")
        self.tape.append(value)

    def stacktrace(self):
        if self._stackfactory is not None:
            self.tape.append(HandleMessage("STACKTRACE", self._stackfactory.delta()))

    def handle(self, name):
        tape = self.tape

        def handle_writer(value):
            tape.append(HandleMessage(name, value))

        return handle_writer

    def bind(self, *a, **kw):
        if not a:
            return None
        index = self._binding_state.bind(a[0])
        self.tape.append(BindingCreate(index))
        return None

    def intern(self, obj):
        self._interned[id(obj)] = obj
        return None

    def async_new_patched(self, obj):
        self._maybe_switch()
        self.tape.append("ASYNC_NEW_PATCHED")
        self.tape.append(self._encode_value(obj))

    def reader(self):
        return MemoryReader(self.tape)


class MemoryReader:
    """Read a protocol tape written by ``MemoryWriter``."""

    def __init__(self, tape, timeout=None, monitor_enabled=False):
        import retracesoftware.utils as utils

        self._type_deserializer = {}
        self._monitor_enabled = monitor_enabled
        self.mark_retraced = utils.noop
        self._tape = tape
        self._tape_len = len(tape)
        self.stub_factory = utils.create_stub_object
        self._source = _MemoryTapeSource(tape)
        self._native_reader = ObjectReader(thread_id=None, source=self._source)
        self._stream = ReplayReader(
            self._native_reader,
            bind=self.bind,
            mark_retraced=self.mark_retraced,
            stub_factory=self.stub_factory,
            monitor_enabled=monitor_enabled,
        )
        self._stream.type_deserializer = self._type_deserializer

    def _get_stream(self):
        return self._stream

    def sync(self):
        self._get_stream().sync()

    def read_result(self):
        return self._get_stream().read_result()

    def bind(self, *a, **kw):
        if not a:
            return None
        return self._native_reader.bind(a[0])

    def bind_if_pending(self, obj):
        try:
            self._native_reader.peek()
        except RuntimeError as exc:
            if "BindingCreate returned when bind was expected" not in str(exc):
                raise
            self._native_reader.bind(obj)
            return True
        return False

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


__all__ = ["MemoryReader", "MemoryWriter"]
