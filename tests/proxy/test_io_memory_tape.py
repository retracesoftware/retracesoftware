from retracesoftware.protocol.record import CALL
from retracesoftware.testing.protocol_memory import MemoryTape


def test_memory_tape_round_trips_call_shape():
    tape = MemoryTape()
    writer = tape.writer()
    reader = tape.reader()

    def fn(value, *, scale=1):
        return value * scale

    writer.bind(fn)
    reader.bind(fn)

    writer.write(CALL, fn, 3, scale=2)

    assert reader.read() == CALL
    assert reader.read() is fn
    assert reader.read() == (3,)
    assert reader.read() == {"scale": 2}


def test_memory_tape_resolves_bound_values_in_payloads():
    tape = MemoryTape()
    writer = tape.writer()
    reader = tape.reader()

    original = object()
    replayed = object()

    writer.bind(original)
    writer.write("RESULT", {"value": [original]})

    reader.bind(replayed)

    assert reader.read() == "RESULT"
    assert reader.read() == {"value": [replayed]}
