"""High-level replay protocol types.

This package sits above ``retracesoftware.stream``.

``stream`` owns low-level transport concerns such as binding records,
thread switching, and raw object movement. ``protocol`` owns the
semantic messages layered on top of that transport, such as
``RESULT``, ``ERROR``, ``ASYNC_CALL``, and ``ASYNC_NEW_PATCHED``.
"""

from .messages import (
    AsyncNewPatchedMessage,
    CallMessage,
    CheckpointMessage,
    ErrorMessage,
    HandleMessage,
    MonitorMessage,
    ResultMessage,
    ThreadSwitchMessage,
)
from .record import stream_writer
from .replay import ReplayReader, next_message

__all__ = [
    "AsyncNewPatchedMessage",
    "CallMessage",
    "CheckpointMessage",
    "ErrorMessage",
    "HandleMessage",
    "MonitorMessage",
    "ReplayReader",
    "ResultMessage",
    "ThreadSwitchMessage",
    "next_message",
    "stream_writer",
]
