"""Record-mode shim."""

from .mode.record import RecordMode


def record_context(system, writer, debug=False, stacktraces=False, on_start=None, on_end=None):
    return RecordMode(
        system,
        writer,
        debug=debug,
        stacktraces=stacktraces,
        on_start=on_start,
        on_end=on_end,
    ).context()
