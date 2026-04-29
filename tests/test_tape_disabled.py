from types import SimpleNamespace

from retracesoftware.tape import RawTapeWriter, create_tape_writer


def test_create_tape_writer_disable_discards_protocol_writes():
    options = SimpleNamespace(recording="disable", format="unframed_binary")

    writer = create_tape_writer(options, ["script.py"], thread_getter=lambda: 0)
    raw_writer = RawTapeWriter(writer)

    raw_writer.write("CALL", "ignored")
    writer.__exit__(None, None, None)
