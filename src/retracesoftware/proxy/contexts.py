"""Public record/replay context builders for the gate-based proxy system."""

from .mode import RecordMode, ReplayMode


def record_context(system, writer, debug=False, stacktraces=False, on_start=None, on_end=None):
    """Build a recording context for *system* and *writer*."""
    return RecordMode(
        system,
        writer,
        debug=debug,
        stacktraces=stacktraces,
        on_start=on_start,
        on_end=on_end,
    ).context()


def replay_context(system, reader, normalize=None, on_start=None, on_end=None):
    """Build a replay context for *system* and *reader*."""
    return ReplayMode(
        system,
        reader,
        normalize=normalize,
        on_start=on_start,
        on_end=on_end,
    ).context()


__all__ = ["record_context", "replay_context"]
