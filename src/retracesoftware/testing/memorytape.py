import traceback
import weakref

from retracesoftware import functional
from retracesoftware.proxy.tape import TapeReader, TapeWriter
import retracesoftware.utils as utils


class _BindingCreate:
    __slots__ = ("index",)

    def __init__(self, index):
        self.index = index

    def __repr__(self):
        return f"BindingCreate({self.index})"

class _BindingLookup:
    __slots__ = ("index",)

    def __init__(self, index):
        self.index = index

    def __repr__(self):
        return f"BindingRef({self.index})"

class _BindingDelete:
    __slots__ = ("index",)

    def __init__(self, index):
        self.index = index

    def __repr__(self):
        return f"BindingDelete({self.index})"

class _BindingState:
    __slots__ = (
        "_binder",
        "_indices",
        "_fallback_bindings",
        "_next_index",
        "_tape_append",
    )

    def __init__(self, tape_append):
        self._binder = utils.Binder(on_delete=self._on_delete)
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
            self._tape_append(_BindingDelete(index))

    def _on_collect(self, obj_id):
        key = ("fallback", obj_id)
        index = self._indices.pop(key, None)
        self._fallback_bindings.pop(obj_id, None)
        if index is not None:
            self._tape_append(_BindingDelete(index))

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
        return _BindingCreate(index)

    def bind(self, obj):
        try:
            binding = self._binder.bind(obj)
        except TypeError:
            return self._bind_fallback(obj)
        return _BindingCreate(self._index_for_key(("binding", binding.handle)))

    def __call__(self, obj, fallback = None):
        binding = self._binder.lookup(obj)
        if binding is not None:
            index = self._indices.get(("binding", binding.handle))
            if index is not None:
                return _BindingLookup(index)
        fallback_index = self._fallback_bindings.get(id(obj))
        if fallback_index is not None:
            return _BindingLookup(fallback_index)
        return fallback(obj) if fallback else obj


class _MemoryTapeWriter:
    """Low-level writer surface used by ``proxy.io.IO`` tests."""

    __slots__ = ("_tape_append", "_bindings", "_write_one")

    def __init__(self, tape_append, serializer = None):
        self._tape_append = tape_append
        self._bindings = _BindingState(tape_append)
        self._write_one = functional.sequence(
            functional.walker(lambda obj: self._bindings(obj, serializer)),
            tape_append,
        )

    def write(self, *values):
        for value in values:
            self._write_one(value)
        return None

    def bind(self, obj):
        self._tape_append(self._bindings.bind(obj))
        return None

class _MemoryTapeReader:
    """Low-level reader surface used by ``proxy.io.IO`` tests."""

    __slots__ = ("_next_from_tape", "_bindings", "read")

    def __init__(self, next_from_tape):
        self._next_from_tape = next_from_tape
        self._bindings = {}
        self.read = functional.sequence(self._next_visible, functional.walker(self._resolve))

    def _next_visible(self):
        value = self._next_from_tape()
        while isinstance(value, _BindingDelete):
            self._bindings.pop(value.index, None)
            value = self._next_from_tape()
        return value

    def _resolve(self, value):
        if isinstance(value, _BindingLookup):
            return self._bindings[value.index]
        elif isinstance(value, _BindingCreate):
            raise RuntimeError(f"unexpected BindingCreate, got {value!r}")
        else:
            return value

    def bind(self, obj):
        marker = self._next_visible()
        if not isinstance(marker, _BindingCreate):
            raise RuntimeError(f"expected BindingCreate, got {marker!r}")
        self._bindings[marker.index] = obj
        return None

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

    def __init__(self, tape=None, serializer = None):
        self.tape = [] if tape is None else list(tape)
        self.serializer = serializer if serializer else default_serializer 

    def writer(self) -> TapeWriter:
        return _MemoryTapeWriter(self.tape.append, self.serializer)

    def reader(self) -> TapeReader:
        return _MemoryTapeReader(iter(self.tape).__next__)


__all__ = ["MemoryTape"]
