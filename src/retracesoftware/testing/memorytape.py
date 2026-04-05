from retracesoftware import functional
from retracesoftware.proxy.tape import TapeReader, TapeWriter


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
        return _BindingCreate(index)

    def __call__(self, obj):
        if id(obj) in self._bindings:
            return _BindingLookup(self._bindings[id(obj)])
        else:
            return obj


class _MemoryTapeWriter:
    """Low-level writer surface used by ``proxy.io.IO`` tests."""

    __slots__ = ("_tape_append", "_bindings", "_write_one")

    def __init__(self, tape_append):
        self._tape_append = tape_append
        self._bindings = _BindingState()
        self._write_one = functional.sequence(
            functional.walker(self._bindings),
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
        self.read = functional.sequence(next_from_tape, functional.walker(self._resolve))

    def _resolve(self, value):
        if isinstance(value, _BindingLookup):
            return self._bindings[value.index]
        elif isinstance(value, _BindingCreate):
            raise RuntimeError(f"expected BindingCreate, got {value!r}")
        else:
            return value

    def bind(self, obj):
        marker = self._next_from_tape()
        if not isinstance(marker, _BindingCreate):
            raise RuntimeError(f"expected BindingCreate, got {marker!r}")
        self._bindings[marker.index] = obj
        return None


class MemoryTape:
    """Low-level in-memory tape with ``write/read`` + ``bind`` surfaces."""

    __slots__ = ("tape",)

    def __init__(self, tape=None):
        self.tape = [] if tape is None else list(tape)

    def writer(self) -> TapeWriter:
        return _MemoryTapeWriter(self.tape.append)

    def reader(self) -> TapeReader:
        return _MemoryTapeReader(iter(self.tape).__next__)


__all__ = ["MemoryTape"]
