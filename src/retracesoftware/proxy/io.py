"""System-backed record/replay entrypoints."""

from contextlib import contextmanager

from retracesoftware.proxy.contracts import AsyncCapture
from retracesoftware.proxy.system import System
from retracesoftware.proxy.taggedtraceio import TaggedTraceReader


def call_recorder(
    *,
    writer,
    debug: bool = False,
    stacktraces: bool = False,
    retrace_space=None,
):
    return recorder(
        writer=writer,
        debug=debug,
        stacktraces=stacktraces,
        retrace_space=retrace_space,
        async_capture=AsyncCapture(thread_switch=False),
    )


def recorder(
    *,
    writer,
    debug: bool = False,
    stacktraces: bool = False,
    retrace_space=None,
    async_capture: AsyncCapture = AsyncCapture(thread_switch=False),
):
    return System.record_system(
        writer=writer,
        debug=debug or stacktraces,
        async_capture=async_capture,
    )


@contextmanager
def recorder_context(**kwargs):
    system = recorder(**kwargs)
    try:
        yield system
    finally:
        system.unpatch_types()


def replayer(
    *,
    next_object,
    close=None,
    on_unexpected=None,
    on_desync=None,
    debug: bool = False,
    stacktraces: bool = False,
    retrace_space=None,
):
    return System.replay_system(
        reader=TaggedTraceReader(next_object, close=close),
        debug=debug or stacktraces,
    )


__all__ = [
    "call_recorder",
    "recorder",
    "recorder_context",
    "replayer",
]
