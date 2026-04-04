import pytest

from retracesoftware.testing.memorytape import MemoryTape


def test_memory_tape_write_is_just_a_flat_append_surface():
    tape = MemoryTape()
    writer = tape.writer()

    writer.write("CALL", "fn", (1, 2), {"scale": 3})

    assert tape.tape == ["CALL", "fn", (1, 2), {"scale": 3}]


def test_memory_tape_bind_and_read_round_trip_nested_bound_values():
    tape = MemoryTape()
    writer = tape.writer()
    reader = tape.reader()

    original = object()
    replayed = object()

    writer.bind(original)
    writer.write({"value": [original, {"nested": (original,)}]})

    reader.bind(replayed)

    assert reader.read() == {"value": [replayed, {"nested": (replayed,)}]}


def test_memory_tape_reader_bind_requires_binding_create_marker():
    tape = MemoryTape(["CALL"])
    reader = tape.reader()

    with pytest.raises(RuntimeError, match="expected BindingCreate"):
        reader.bind(object())


def test_memory_tape_reader_rejects_unconsumed_binding_create():
    tape = MemoryTape()
    writer = tape.writer()
    reader = tape.reader()

    writer.bind(object())

    with pytest.raises(RuntimeError, match="expected BindingCreate"):
        reader.read()


def test_memory_tape_reader_raises_stop_iteration_when_exhausted():
    tape = MemoryTape()
    writer = tape.writer()
    reader = tape.reader()

    writer.write("ONLY")

    assert reader.read() == "ONLY"
    with pytest.raises(StopIteration):
        reader.read()
