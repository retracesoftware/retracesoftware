"""High-level replay protocol types.

This package sits above ``retracesoftware.stream``.

``stream`` owns low-level transport concerns such as binding records,
thread switching, and raw object movement. ``protocol`` owns the
semantic messages layered on top of that transport, such as
``CALL``, ``RESULT``, ``ERROR``, ``ASYNC_CALL``, and
``ASYNC_NEW_PATCHED``.
"""

from .messages import (
    AsyncNewPatchedMessage,
    CallMessage,
    CheckpointMessage,
    ErrorMessage,
    HandleMessage,
    MonitorMessage,
    ResultMessage,
    StacktraceMessage,
    ThreadSwitchMessage,
)
from .normalize import normalize
from .record import CALL, stream_writer
from .replay import ReplayReader, next_message

__all__ = [
    "AsyncNewPatchedMessage",
    "CallMessage",
    "CALL",
    "CheckpointMessage",
    "ErrorMessage",
    "HandleMessage",
    "MonitorMessage",
    "normalize",
    "ReplayReader",
    "ResultMessage",
    "StacktraceMessage",
    "ThreadSwitchMessage",
    "next_message",
    "stream_writer",
]
