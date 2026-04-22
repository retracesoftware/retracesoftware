import gc
import pytest

from retracesoftware.proxy.tape import TapeReader, TapeWriter
from retracesoftware.testing.memorytape import MemoryTape, _BindCloseMarker
import retracesoftware.stream as stream


def test_memory_tape_write_is_just_a_flat_append_surface():
    tape = MemoryTape()
    writer = tape.writer()

    assert isinstance(writer, TapeWriter)

    writer.write("CALL", "fn", (1, 2), {"scale": 3})

    assert tape.tape == ["CALL", "fn", (1, 2), {"scale": 3}]


def test_memory_tape_bind_and_read_round_trip_nested_bound_values():
    tape = MemoryTape()
    writer = tape.writer()
    reader = tape.reader()

    assert isinstance(writer, TapeWriter)
    assert isinstance(reader, TapeReader)

    original = object()
    replayed = object()

    writer.bind(original)
    writer.write({"value": [original, {"nested": (original,)}]})

    reader.bind(replayed)

    assert reader.read() == {"value": [replayed, {"nested": (replayed,)}]}


def test_memory_tape_uses_tape_local_binding_indices_not_global_binder_handles():
    class Thing:
        pass

    binder = stream.Binder()
    binder.bind(Thing())

    tape = MemoryTape()
    writer = tape.writer()

    first = Thing()
    second = Thing()

    writer.bind(first)
    writer.bind(second)
    writer.write((first, second))

    assert tape.tape[0].index == 0
    assert tape.tape[1].index == 1
    assert repr(tape.tape[2][0]) == "Binding(0)"
    assert repr(tape.tape[2][1]) == "Binding(1)"


def test_memory_tape_reader_bind_requires_binding_create_marker():
    tape = MemoryTape(["CALL"])
    reader = tape.reader()

    with pytest.raises(RuntimeError, match="expected bind marker"):
        reader.bind(object())


def test_memory_tape_reader_rejects_unconsumed_binding_create():
    tape = MemoryTape()
    writer = tape.writer()
    reader = tape.reader()

    writer.bind(object())

    with pytest.raises(RuntimeError, match="expected bind marker"):
        reader.read()


def test_memory_tape_reader_raises_stop_iteration_when_exhausted():
    tape = MemoryTape()
    writer = tape.writer()
    reader = tape.reader()

    writer.write("ONLY")

    assert reader.read() == "ONLY"
    with pytest.raises(StopIteration):
        reader.read()


def test_memory_tape_writer_emits_binding_delete_for_weakrefable_bound_object():
    class Thing:
        pass

    tape = MemoryTape()
    writer = tape.writer()

    value = Thing()
    writer.bind(value)

    del value
    gc.collect()

    assert any(isinstance(item, _BindCloseMarker) for item in tape.tape)


def test_memory_tape_reader_consumes_binding_delete_records():
    class Thing:
        pass

    tape = MemoryTape()
    writer = tape.writer()
    reader = tape.reader()

    original = Thing()
    replayed = Thing()

    writer.bind(original)
    writer.write(original)
    del original
    gc.collect()
    writer.write("AFTER")

    reader.bind(replayed)

    assert reader.read() is replayed
    assert reader.read() == "AFTER"
